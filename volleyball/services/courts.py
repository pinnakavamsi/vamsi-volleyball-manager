from datetime import timedelta,time,datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select,text
from ..db import get_db
from ..models import CourtAssignment,Court,Match,Venue,Tournament,Entry,Team
from ..common import APIError,now,audit,emit


def assignment(match):
    return get_db().scalar(select(CourtAssignment).where(CourtAssignment.match_id==match.id,CourtAssignment.released_at.is_(None)))

def check_court(court,at=None):
    if not court or not court.available:raise APIError('Court is unavailable. Choose an eligible court.',409,'court_unavailable')
    venue=get_db().get(Venue,court.venue_id)
    local=(at or now()).astimezone(ZoneInfo(venue.timezone))
    settings=venue.settings or {}
    if settings.get('opening_hours'):
        hours=settings['opening_hours'].get(str(local.weekday()),settings['opening_hours'].get('default'))
        if hours is None:raise APIError('Venue is closed on this day.',409,'venue_closed')
        try:
            if not time.fromisoformat(hours['open'])<=local.time().replace(tzinfo=None)<time.fromisoformat(hours['close']):raise APIError('Venue is outside its opening hours.',409,'venue_closed')
        except (KeyError,ValueError):raise APIError('Configure valid venue opening hours before assigning this court.')
    for closure in [*(venue.settings or {}).get('closures',[]),*(court.settings or {}).get('closures',[])]:
        if closure.get('date')==local.date().isoformat():raise APIError('Venue/court is closed on this date.',409,'court_closed')
        if closure.get('start') and closure.get('end'):
            if datetime.fromisoformat(closure['start']) <= (at or now()) < datetime.fromisoformat(closure['end']):
                raise APIError('Venue/court is closed during this interval.',409,'court_closed')

def assign_court(match,court_id,user_id=None,reason=None):
    db=get_db()
    if match.status in ('ended','cancelled','abandoned'):raise APIError('Closed games cannot reserve a court.',409)
    old=assignment(match)
    if old and old.court_id==court_id:return old
    if match.status in ('in_progress','paused') and not reason:raise APIError('Moving an active game requires confirmation and a reason.')
    # Every caller locks its match first, then all courts in UUID order.
    ids=sorted(set([court_id]+([old.court_id] if old else [])))
    courts={c.id:c for c in db.scalars(select(Court).where(Court.id.in_(ids),Court.organization_id==match.organization_id).order_by(Court.id).with_for_update())}
    court=courts.get(court_id);check_court(court)
    occupied=db.scalar(select(CourtAssignment).where(CourtAssignment.court_id==court_id,CourtAssignment.released_at.is_(None)))
    if occupied:
        other=db.get(Match,occupied.match_id)
        if occupied.status=='reserved' and occupied.expires_at and occupied.expires_at<now() and other.status not in ('in_progress','paused'):
            occupied.released_at=now();db.flush()
        else:raise APIError('This court is reserved or occupied by another game.',409,'court_conflict',{'match_id':occupied.match_id})
    if old:old.released_at=now();db.flush()
    row=CourtAssignment(organization_id=match.organization_id,match_id=match.id,court_id=court_id,assigned_by=user_id,
        status='occupied' if match.status in ('in_progress','paused') else 'reserved',
        expires_at=None if match.status in ('in_progress','paused') else now()+timedelta(minutes=30),
        started_at=now() if match.status in ('in_progress','paused') else None,reason=reason)
    db.add(row)
    if match.status=='scheduled':match.status='ready'
    match.version+=1
    audit('court.assign','match',match.id,before={'court_id':old.court_id if old else None},after={'court_id':court_id},reason=reason)
    emit(match,'court.changed');db.flush();return row

def release_court(match,reason=None,ending=False):
    db=get_db();row=assignment(match)
    if not row:return
    db.scalar(select(Court).where(Court.id==row.court_id).with_for_update())
    if not ending and match.status in ('in_progress','paused'):raise APIError('An active or paused game keeps its court until an explicit outcome.',409)
    row.released_at=now();row.reason=reason or row.reason
    if match.status=='ready':match.status='scheduled'
    audit('court.release','match',match.id,reason=reason)

def start_court(match):
    db=get_db();row=assignment(match)
    if not row:raise APIError('Assign an available court before Start Game.',409,'court_required')
    court=db.scalar(select(Court).where(Court.id==row.court_id).with_for_update());check_court(court)
    if row.expires_at and row.expires_at<now():raise APIError('The court reservation expired. Release and assign it again.',409,'reservation_expired')
    # Serialize starts involving the same reusable team, even across events.
    team_ids=set(db.scalars(select(Entry.team_id).where(Entry.id.in_([match.entry_a_id,match.entry_b_id]))))
    list(db.scalars(select(Team).where(Team.id.in_(team_ids)).order_by(Team.id).with_for_update()))
    if match.referee_name:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':str(match.organization_id)+':referee:'+match.referee_name.strip().casefold()})
    # Live overlap is forbidden independently from schedule estimates.
    active=list(db.scalars(select(Match).where(Match.organization_id==match.organization_id,Match.id!=match.id,Match.status.in_(['in_progress','paused']))))
    for other in active:
        other_teams=set(db.scalars(select(Entry.team_id).where(Entry.id.in_([other.entry_a_id,other.entry_b_id]))))
        if team_ids & other_teams:
            raise APIError('A participating team is already playing another game.',409,'team_conflict')
        if match.referee_name and match.referee_name.strip().casefold()==(other.referee_name or '').strip().casefold():
            raise APIError('The assigned referee is already conducting another game.',409,'referee_conflict')
    row.status='occupied';row.expires_at=None;row.started_at=now()
