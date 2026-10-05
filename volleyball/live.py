"""Bounded public projections for polling after the initial event overview."""
from copy import deepcopy
from flask import Blueprint
from sqlalchemy import select, or_
from sqlalchemy.orm import load_only
from .common import APIError, iso, now
from .competition import public_tournament
from .db import get_db
from .models import Match, Entry, Court, CourtAssignment, Venue
from .scoring import elapsed
from .services.courts import check_court
from .services.public_config import public_configuration_version

bp=Blueprint('live',__name__,url_prefix='/api/v1')
MAX_LIVE_MATCHES=1000


def _participant(entry):
    return {'id':entry.id,'team_id':entry.team_id,'name':entry.name,'color':entry.color} if entry else None


@bp.get('/public/<slug>/live')
def public_live(slug):
    # Resolving the slug establishes the authorized RLS scope and rechecks
    # publication/visibility on every poll, including otherwise unchanged data.
    tournament=public_tournament(slug);db=get_db()
    matches=list(db.scalars(select(Match).options(load_only(Match.id,Match.tournament_id,Match.division_id,Match.stage_id,
        Match.entry_a_id,Match.entry_b_id,Match.version,Match.generation,Match.state,Match.status,Match.winner_id,
        Match.result_status,Match.outcome,Match.result_revision,Match.planned_at,Match.planned_duration,Match.actual_start,Match.actual_end))
        .where(Match.tournament_id==tournament.id)
        .order_by(Match.planned_at,Match.round_number,Match.id).limit(MAX_LIVE_MATCHES+1)))
    stamp=now()
    response={'configuration_version':public_configuration_version(tournament),
        'standings_generation':tournament.standings_generation,'updated_at':iso(stamp)}
    if len(matches)>MAX_LIVE_MATCHES:
        # Do not return a truncated ID set which a viewer could mistake for
        # removed games. The full overview remains independently bounded.
        return {**response,'full_refresh':True,'matches':[],'courts':[]}

    match_ids=[m.id for m in matches]
    closed_ids=[m.id for m in matches if m.status in ('ended','cancelled','abandoned')]
    assignments={a.match_id:a for a in db.scalars(select(CourtAssignment).where(
        CourtAssignment.match_id.in_(match_ids),or_(CourtAssignment.released_at.is_(None),CourtAssignment.match_id.in_(closed_ids)))
        .distinct(CourtAssignment.match_id).order_by(CourtAssignment.match_id,
            CourtAssignment.released_at.is_(None).desc(),CourtAssignment.created_at.desc()))} if match_ids else {}
    entry_ids={value for m in matches for value in (m.entry_a_id,m.entry_b_id) if value}
    entries={e.id:e for e in db.scalars(select(Entry).options(load_only(Entry.id,Entry.team_id,Entry.name,Entry.color))
        .where(Entry.id.in_(entry_ids)))} if entry_ids else {}

    venue_ids=set((tournament.settings or {}).get('venue_ids',[]))
    if not venue_ids:
        venue_ids=set(db.scalars(select(Court.venue_id).join(CourtAssignment,CourtAssignment.court_id==Court.id)
            .join(Match,Match.id==CourtAssignment.match_id).where(Match.tournament_id==tournament.id).distinct()))
    assigned_court_ids={a.court_id for a in assignments.values()}
    courts={c.id:c for c in db.scalars(select(Court).options(load_only(Court.id,Court.venue_id,Court.name,Court.available,Court.settings))
        .where(or_(Court.venue_id.in_(venue_ids),Court.id.in_(assigned_court_ids))))} if venue_ids or assigned_court_ids else {}
    display_courts=[c for c in courts.values() if c.venue_id in venue_ids]
    court_venue_ids={c.venue_id for c in display_courts}
    # Strong references preserve the identity-map lookup used by check_court;
    # no per-court SELECT is needed for venue opening hours/closures.
    venues={v.id:v for v in db.scalars(select(Venue).options(load_only(Venue.id,Venue.timezone,Venue.settings))
        .where(Venue.id.in_(court_venue_ids)))} if court_venue_ids else {}
    active={a.court_id:a for a in db.scalars(select(CourtAssignment).where(
        CourtAssignment.court_id.in_([c.id for c in display_courts]),CourtAssignment.released_at.is_(None)))} if display_courts else {}
    court_rows=[]
    for court in display_courts:
        assignment=active.get(court.id)
        if assignment and assignment.status=='occupied':status='occupied'
        elif assignment and (assignment.expires_at is None or assignment.expires_at>stamp):status='reserved'
        else:
            try:
                check_court(court,stamp);status='available'
            except APIError:status='unavailable'
        court_rows.append({'id':court.id,'venue_id':court.venue_id,'name':court.name,'available':court.available,'status':status})

    rows=[]
    for match in matches:
        assignment=assignments.get(match.id);court=courts.get(assignment.court_id) if assignment else None
        rows.append({'id':match.id,'tournament_id':match.tournament_id,'division_id':match.division_id,'stage_id':match.stage_id,
            'version':match.version,'generation':match.generation,'state':deepcopy(match.state or {}),
            'elapsed_seconds':elapsed(match),'status':match.status,'winner_id':match.winner_id,
            'result_status':match.result_status,'outcome':match.outcome,'result_revision':match.result_revision,
            'planned_at':iso(match.planned_at),'planned_duration':match.planned_duration,
            'actual_start':iso(match.actual_start),'actual_end':iso(match.actual_end),
            'team_a':_participant(entries.get(match.entry_a_id)),'team_b':_participant(entries.get(match.entry_b_id)),
            'court':{'id':court.id,'name':court.name,'venue_id':court.venue_id} if court else None})
    return {**response,'matches':rows,'courts':court_rows,'full_refresh':False}
