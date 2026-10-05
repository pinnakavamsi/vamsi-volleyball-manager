"""Scoped competition setup, reviewable fixtures and audited results."""
import re,secrets,hashlib,io
from copy import deepcopy
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import qrcode
import qrcode.image.svg
from flask import Blueprint,g,request,current_app,Response,stream_with_context
from sqlalchemy import select,func,text,or_,update
from .auth import require_org,authorize_tournament,scoped_tournament_ids,valid_id
from .common import APIError,body,now,iso,serialize,audit,emit
from .db import get_db,set_scope
from .models import (Tournament,Division,Stage,Entry,Team,Match,Court,CourtAssignment,Venue,
    Organization,Grant,ScorerSession,ResultRevision,Announcement,Outbox,MatchEvent,RuleVersion,TournamentVenue)
from .services.rules import DEFAULT_RULES,validate_rules,effective_rules,set_winner,match_winner
from .services.rankings import standings,compare_pools
from .services.formats import FORMATS,round_robin,bracket
from .services.courts import assign_court,release_court,assignment
from .scoring import elapsed,stop_clock,initial_state
from .locations import guard_venue_removal,tournament_venue_ids,validate_venue_ids
bp=Blueprint('competition',__name__,url_prefix='/api/v1')

def owned(cls,identifier,lock=False):
    q=select(cls).where(cls.id==valid_id(identifier),cls.organization_id==g.organization.id)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    row=get_db().scalar(q)
    if not row:raise APIError('Record not found in this organization.',404,'not_found')
    if cls is Tournament:authorize_tournament(row)
    elif hasattr(row,'tournament_id'):authorize_tournament(get_db().get(Tournament,row.tournament_id))
    return row

def name(value,label='Name',limit=180):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise APIError(f'{label} is required and must be at most {limit} characters.')
    return value.strip()
def instant(value):
    if value is None:return None
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
        return dt
    except (ValueError,TypeError):raise APIError('Use an ISO timestamp with a timezone offset.')
def day(value):
    try:return date.fromisoformat(value)
    except (ValueError,TypeError):raise APIError('Use an ISO calendar date (YYYY-MM-DD).')
def tz(value):
    try:ZoneInfo(value);return value
    except (ValueError,KeyError,TypeError):raise APIError('Use a valid IANA timezone.')
def bounded_integer(value,label,low=1,high=1000):
    if not isinstance(value,int) or isinstance(value,bool) or not low<=value<=high:raise APIError(f'{label} must be an integer from {low} to {high}.')
    return value

def match_json(m,public=False,context=None):
    """Serialize one game, or use the containing response's preloaded records."""
    db=get_db()
    if context is None:
        a=db.get(Entry,m.entry_a_id) if m.entry_a_id else None;b=db.get(Entry,m.entry_b_id) if m.entry_b_id else None
        ca=assignment(m)
        if ca is None and m.status in ('ended','cancelled','abandoned'):ca=db.scalar(select(CourtAssignment).where(CourtAssignment.match_id==m.id).order_by(CourtAssignment.created_at.desc()).limit(1))
        court=db.get(Court,ca.court_id) if ca else None
        t=db.get(Tournament,m.tournament_id);d=db.get(Division,m.division_id);s=db.get(Stage,m.stage_id)
        org=db.get(Organization,m.organization_id)
    else:
        a=context['entries'].get(m.entry_a_id);b=context['entries'].get(m.entry_b_id)
        ca=context['assignments'].get(m.id);court=context['courts'].get(ca.court_id) if ca else None
        t=context['tournaments'][m.tournament_id];d=context['divisions'][m.division_id];s=context['stages'][m.stage_id]
        org=context['organizations'][m.organization_id]
    rules=m.rule_snapshot or effective_rules(t,d,s,m)
    state=deepcopy(m.state or initial_state(rules))
    def participant(e):return {'id':e.id,'team_id':e.team_id,'name':e.name,'color':e.color} if e else None
    result={'id':m.id,'tournament_id':m.tournament_id,'division_id':m.division_id,'stage_id':m.stage_id,
        'team_a':participant(a),'team_b':participant(b),'court':{'id':court.id,'name':court.name,'venue_id':court.venue_id} if court else None,
        'planned_at':iso(m.planned_at),'planned_duration':m.planned_duration,'actual_start':iso(m.actual_start),'actual_end':iso(m.actual_end),
        'status':m.status,'version':m.version,'generation':m.generation,'rules':rules,'rule_snapshot':bool(m.rule_snapshot),
        'state':state,'winner_id':m.winner_id,'outcome':m.outcome,'result_status':m.result_status,'result_revision':m.result_revision,
        'elapsed_seconds':elapsed(m),'round_number':m.round_number,'pool':m.pool,'source_a_id':m.source_a_id,'source_b_id':m.source_b_id,
        'division_name':d.name,'stage_name':s.name,'stage_format':s.format,'tournament_name':t.name,'organization_name':org.name}
    if not public:
        result['referee_name']=m.referee_name
        scorer=(context['scorers'].get((m.id,m.generation)) if context is not None else
            db.scalar(select(ScorerSession).where(ScorerSession.match_id==m.id,ScorerSession.revoked_at.is_(None),ScorerSession.generation==m.generation)))
        result['scorer']={'name':scorer.name,'last_seen_at':iso(scorer.last_seen_at),'connected':bool(scorer.last_seen_at and (now()-scorer.last_seen_at).total_seconds()<20)} if scorer else None
    if public and not (t.settings or {}).get('public_rules',True):result.pop('rules',None)
    return result

def tournament_detail(t,public=False):
    db=get_db();ds=list(db.scalars(select(Division).where(Division.tournament_id==t.id).order_by(Division.created_at)))
    stages=list(db.scalars(select(Stage).where(Stage.tournament_id==t.id).order_by(Stage.position)))
    es=list(db.scalars(select(Entry).where(Entry.tournament_id==t.id).order_by(Entry.seed)))
    ms=list(db.scalars(select(Match).where(Match.tournament_id==t.id).order_by(Match.planned_at,Match.round_number).limit(1000)))
    venue_ids=tournament_venue_ids(t)
    all_venues=list(db.scalars(select(Venue).where(Venue.organization_id==t.organization_id)))
    vs=[v for v in all_venues if v.id in venue_ids]
    cs=list(db.scalars(select(Court).where(Court.venue_id.in_([v.id for v in vs])))) if vs else []
    org=db.get(Organization,t.organization_id)
    announcements=list(db.scalars(select(Announcement).where(Announcement.tournament_id==t.id,Announcement.publish_at<=now(),or_(Announcement.expires_at.is_(None),Announcement.expires_at>now())).order_by(Announcement.publish_at.desc())))
    tr=serialize(t)
    if public:
        tr['settings']={k:v for k,v in (t.settings or {}).items() if k in ('public_rules','public_reports','public_sections','sponsors')}
        if not (t.settings or {}).get('public_rules',True):tr.pop('rules',None)
    from .admin import court_json
    from .services.public_config import public_configuration_version
    active_assignments={a.court_id:a for a in db.scalars(select(CourtAssignment).where(CourtAssignment.court_id.in_([c.id for c in cs]),CourtAssignment.released_at.is_(None)))}
    # Select one assignment per game in PostgreSQL. Active assignments take
    # priority; closed games retain their most recent historical court.
    match_ids=[m.id for m in ms];closed_ids=[m.id for m in ms if m.status in ('ended','cancelled','abandoned')]
    match_assignments={a.match_id:a for a in db.scalars(select(CourtAssignment).where(
        CourtAssignment.match_id.in_(match_ids),or_(CourtAssignment.released_at.is_(None),CourtAssignment.match_id.in_(closed_ids)))
        .distinct(CourtAssignment.match_id).order_by(CourtAssignment.match_id,CourtAssignment.released_at.is_(None).desc(),CourtAssignment.created_at.desc()))} if match_ids else {}
    courts={c.id:c for c in cs};extra_court_ids={a.court_id for a in match_assignments.values()}-courts.keys()
    if extra_court_ids:courts.update({c.id:c for c in db.scalars(select(Court).where(Court.id.in_(extra_court_ids)))})
    context={'entries':{e.id:e for e in es},'divisions':{d.id:d for d in ds},'stages':{s.id:s for s in stages},
        'tournaments':{t.id:t},'organizations':{org.id:org},'assignments':match_assignments,'courts':courts,
        'venues':{v.id:v for v in all_venues},'scorers':{},'matches':{m.id:m for m in ms}}
    if not public and match_ids:
        context['scorers']={(s.match_id,s.generation):s for s in db.scalars(select(ScorerSession).where(
            ScorerSession.match_id.in_(match_ids),ScorerSession.revoked_at.is_(None)))}
    # court_json applies the existing assignment-visibility gate and opening
    # hours checks. Holding venues and relevant matches in this context lets
    # its db.get calls use the identity map without individual SQL queries.
    scopes=scoped_tournament_ids() if getattr(g,'membership',None) else None
    if scopes:
        extra_match_ids={a.match_id for a in active_assignments.values()}-context['matches'].keys()
        if extra_match_ids:context['matches'].update({m.id:m for m in db.scalars(select(Match).where(Match.id.in_(extra_match_ids)))})
    court_results={c.id:court_json(c,active_assignments.get(c.id)) for c in cs}
    result={'tournament':tr,'organization':{'id':org.id,'name':org.name,'color':org.color,'logo':org.logo,'demo':org.demo},'demo':org.demo,
      'divisions':[({'id':d.id,'name':d.name,'category':d.category,**({'rules':d.rules} if (t.settings or {}).get('public_rules',True) else {})} if public else serialize(d)) for d in ds],'stages':[({'id':s.id,'division_id':s.division_id,'name':s.name,'format':s.format,'position':s.position,**({'rules':s.rules} if (t.settings or {}).get('public_rules',True) else {})} if public else serialize(s)) for s in stages],
      'entries':[({'id':e.id,'team_id':e.team_id,'division_id':e.division_id,'name':e.name,'color':e.color,'seed':e.seed,'pool':e.pool,'status':e.status} if public else serialize(e)) for e in es],
      'matches':[match_json(m,public,context) for m in ms],'courts':[({'id':c.id,'venue_id':c.venue_id,'name':c.name,'available':c.available,'status':court_results[c.id]['status']} if public else court_results[c.id]) for c in cs],
      'venues':[{'id':v.id,'name':v.name,'address':v.address,'latitude':v.latitude,'longitude':v.longitude,'timezone':v.timezone,'indoor':v.indoor} for v in vs],
      'standings':[standings(d) for d in ds],'announcements':[{'id':a.id,'title':a.title,'content':a.content,'visibility':a.visibility,'publish_at':iso(a.publish_at)} for a in announcements if not public or a.visibility=='public'],
      'display':t.display or {},'configuration_version':public_configuration_version(t),'updated_at':iso(now())}
    if public and not (t.settings or {}).get('public_rules',True):
        for ranking in result['standings']:ranking.pop('policy',None)
    return result

def public_tournament(slug):
    db=get_db();row=db.execute(text('SELECT tournament_id,organization_id FROM resolve_public_tournament(:slug)'),{'slug':slug}).first()
    if not row:raise APIError('This tournament is private or unavailable.',404,'private_tournament')
    set_scope(row.organization_id);t=db.get(Tournament,str(row.tournament_id))
    if not t or t.visibility!='public' or t.status=='draft':raise APIError('Tournament unavailable.',404)
    g.organization=db.get(Organization,t.organization_id);return t

RANKING_KEYS={'win_points','loss_points','draw_points','forfeit_win_points','forfeit_loss_points','include_forfeit_scores','tie_breaks','head_to_head','restart_tie_breaks','quotient_zero','cross_pool'}
def record_rules(obj,reason):
    db=get_db();scope=type(obj).__name__.lower();tournament_id=obj.id if isinstance(obj,Tournament) else obj.tournament_id
    version=(db.scalar(select(func.max(RuleVersion.version)).where(RuleVersion.scope_type==scope,RuleVersion.scope_id==obj.id)) or 0)+1
    db.add(RuleVersion(organization_id=obj.organization_id,tournament_id=tournament_id,division_id=obj.id if isinstance(obj,Division) else getattr(obj,'division_id',None),stage_id=obj.id if isinstance(obj,Stage) else None,scope_type=scope,scope_id=obj.id,version=version,rules=deepcopy(obj.rules),reason=reason,created_by=g.user.id))

def sync_venues(t,venue_ids):
    venue_ids=validate_venue_ids(venue_ids)
    db=get_db();existing={v.venue_id:v for v in db.scalars(select(TournamentVenue).where(TournamentVenue.tournament_id==t.id))}
    guard_venue_removal(t,[venue_id for venue_id,row in existing.items() if row.active and venue_id not in venue_ids])
    for venue_id,row in existing.items():row.active=venue_id in venue_ids
    for venue_id in venue_ids:
        owned(Venue,venue_id)
        if venue_id not in existing:db.add(TournamentVenue(organization_id=t.organization_id,tournament_id=t.id,venue_id=venue_id,active=True))

def guard_ranking_change(obj,new_rules,data):
    db=get_db();t=obj if isinstance(obj,Tournament) else db.get(Tournament,obj.tournament_id)
    d=obj if isinstance(obj,Division) else db.get(Division,obj.division_id) if isinstance(obj,Stage) else None
    stage=obj if isinstance(obj,Stage) else None
    before=effective_rules(t,d,stage);previous=obj.rules;obj.rules=new_rules;after=effective_rules(t,d,stage);obj.rules=previous
    changed={k:{'before':before.get(k),'after':after.get(k)} for k in RANKING_KEYS if before.get(k)!=after.get(k)}
    if not changed:return
    db=get_db();q=select(Match.id).where(Match.result_status=='finalized',Match.outcome!='bye')
    q=q.where(Match.tournament_id==obj.id) if isinstance(obj,Tournament) else q.where(Match.division_id==obj.id) if isinstance(obj,Division) else q.where(Match.stage_id==obj.id)
    if not db.scalar(q.limit(1)):return
    if data.get('recalculate_history') is not True or not str(data.get('reason','')).strip():
        raise APIError('Historical ranking policy changes require a reviewed preview, explicit recalculation and a reason.',409,'ranking_review_required',{'changed_fields':changed,'preview_endpoint':'/api/v1/rules/preview'})
    t=obj if isinstance(obj,Tournament) else db.get(Tournament,obj.tournament_id);t.standings_generation+=1
    audit('ranking.policy_recalculated',type(obj).__name__.lower(),obj.id,after=changed,reason=data['reason'])

@bp.post('/rules/preview')
@require_org('manage')
def preview_rules():
    data=body();kind=data.get('scope_type');cls={'tournament':Tournament,'division':Division,'stage':Stage}.get(kind)
    if not cls:raise APIError('Choose tournament, division or stage rule scope.')
    obj=owned(cls,data.get('scope_id'),True);new=validate_rules(data.get('rules'),partial=cls is not Tournament)
    db=get_db();t=obj if cls is Tournament else db.get(Tournament,obj.tournament_id)
    divisions=list(db.scalars(select(Division).where(Division.tournament_id==t.id))) if cls is Tournament else [obj if cls is Division else db.get(Division,obj.division_id)]
    before=[standings(d,obj.id if cls is Stage else None) for d in divisions];previous=deepcopy(obj.rules);obj.rules=new
    after=[standings(d,obj.id if cls is Stage else None) for d in divisions];obj.rules=previous
    return {'before':before,'after':after,'rules':new,'reason_required':True,'explicit_recalculation_required':True,'current_generation':t.standings_generation}

@bp.route('/tournaments',methods=['GET','POST'])
@require_org('read')
def tournaments():
    db=get_db()
    if request.method=='GET':
        q=select(Tournament).where(Tournament.organization_id==g.organization.id).order_by(Tournament.start_date.desc())
        ids=scoped_tournament_ids()
        if ids is not None:q=q.where(Tournament.id.in_(ids))
        rows=list(db.scalars(q.limit(200)));return {'items':[serialize(t) for t in rows],'total':len(rows)}
    if g.membership.role not in ('admin','manager') or g.membership.tournament_ids:raise APIError('An organization administrator or unscoped tournament manager can create events.',403)
    data=body();n=name(data.get('name'));slug=data.get('slug') or re.sub(r'[^a-z0-9]+','-',n.lower()).strip('-')+'-'+secrets.token_hex(3)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,139}',slug):raise APIError('Slug must contain lowercase letters, numbers and hyphens.')
    start=day(data.get('start_date'));end=day(data.get('end_date'))
    if end<start:raise APIError('End date must be on or after start date.')
    kind=data.get('competition_type','tournament')
    if kind not in ('tournament','league'):raise APIError('Choose tournament or league.')
    settings=data.get('settings',{})
    if not isinstance(settings,dict):raise APIError('Settings must be an object.')
    if 'venue_ids' in settings:settings={**settings,'venue_ids':validate_venue_ids(settings['venue_ids'])}
    for vid in settings.get('venue_ids',[]):owned(Venue,vid)
    row=Tournament(organization_id=g.organization.id,name=n,slug=slug,description=str(data.get('description',''))[:5000],start_date=start,end_date=end,
        timezone=tz(data.get('timezone',g.organization.timezone)),competition_type=kind,rules=validate_rules(data.get('rules',DEFAULT_RULES)),settings=settings,status='draft',visibility='private')
    db.add(row);db.flush();record_rules(row,'Initial reviewed editable defaults');sync_venues(row,settings.get('venue_ids',[]));audit('tournament.create','tournament',row.id,after=serialize(row));db.commit();return serialize(row),201

@bp.route('/tournaments/<identifier>',methods=['GET','PATCH'])
@require_org('read')
def tournament(identifier):
    db=get_db();t=owned(Tournament,identifier,lock=request.method=='PATCH')
    if request.method=='GET':return tournament_detail(t)
    if g.membership.role not in ('admin','manager'):raise APIError('Tournament management permission is required.',403)
    data=body();before=serialize(t);prior_venue_ids=tournament_venue_ids(t)
    for key in ('name','description','timezone','start_date','end_date','visibility','status','rules','settings'):
        if key not in data:continue
        value=data[key]
        if key=='name':value=name(value)
        elif key=='description':value=str(value)[:5000]
        elif key=='timezone':value=tz(value)
        elif key in ('start_date','end_date'):value=day(value)
        elif key=='visibility' and value not in ('private','public'):raise APIError('Choose private or public visibility.')
        elif key=='status':
            if value not in ('draft','published','active','completed','archived'):raise APIError('Invalid tournament lifecycle.')
            if value=='completed' and db.scalar(select(Match.id).where(Match.tournament_id==t.id,Match.status.in_(['in_progress','paused']))):raise APIError('End active games before completing the tournament.',409)
        elif key=='rules':
            value=validate_rules(value);guard_ranking_change(t,value,data)
        elif key=='settings':
            if not isinstance(value,dict):raise APIError('Settings must be an object.')
            if 'venue_ids' in value:
                value={**value,'venue_ids':validate_venue_ids(value['venue_ids'])}
                guard_venue_removal(t,[vid for vid in prior_venue_ids if vid not in value['venue_ids']])
            for vid in value.get('venue_ids',[]):owned(Venue,vid)
            value={**(t.settings or {}),**value}
        setattr(t,key,value)
    if 'rules' in data:record_rules(t,data.get('reason','Explicit setup revision'))
    if 'settings' in data and 'venue_ids' in data['settings']:sync_venues(t,t.settings.get('venue_ids',[]))
    if t.end_date<t.start_date:raise APIError('Tournament date range is invalid.')
    if t.status=='draft' and t.visibility=='public':raise APIError('Review and publish a draft before making it public.')
    if data.get('status') in ('published','active'):
        errors=validation_errors(t)
        if errors:raise APIError('Resolve setup errors before publication or activation.',400,details=errors)
    audit('tournament.update','tournament',t.id,before=before,after=serialize(t),reason=data.get('reason'))
    db.add(Outbox(organization_id=t.organization_id,tournament_id=t.id,kind='tournament.changed',version=t.standings_generation,payload={'visibility':t.visibility}))
    db.commit();return serialize(t)

def validation_errors(t):
    db=get_db();errors=[]
    try:effective_rules(t)
    except APIError as e:errors.append(e.message)
    divisions=list(db.scalars(select(Division).where(Division.tournament_id==t.id)))
    if not divisions:errors.append('Add at least one division.')
    for d in divisions:
        entries=list(db.scalars(select(Entry).where(Entry.division_id==d.id,Entry.status=='active')))
        if len(entries)<2:errors.append(f'{d.name}: add at least two active teams.')
        stages=list(db.scalars(select(Stage).where(Stage.division_id==d.id)))
        if not stages:errors.append(f'{d.name}: configure a competition stage.')
        for s in stages:
            try:effective_rules(t,d,s)
            except APIError as e:errors.append(f'{d.name}/{s.name}: {e.message}')
            source_id=(s.settings or {}).get('source_stage_id')
            if source_id:
                source=next((candidate for candidate in stages if candidate.id==source_id),None)
                if not source or source.id==s.id or source.position>=s.position or source.format not in ('league','round_robin','pools_knockout') or s.format!='single_elimination':
                    errors.append(f'{d.name}/{s.name}: choose an earlier round-robin, league or pool stage in this division as the knockout qualification source.')
    matches=list(db.scalars(select(Match).where(Match.tournament_id==t.id)))
    for m in matches:
        if m.planned_at:
            local=m.planned_at.astimezone(ZoneInfo(t.timezone)).date()
            if not t.start_date<=local<=t.end_date:errors.append(f'Game {m.id}: planned time falls outside the event dates.')
            errors.extend(schedule_conflicts(m))
        if m.source_a_id==m.id or m.source_b_id==m.id:errors.append('A fixture cannot depend on itself.')
    return list(dict.fromkeys(errors))

@bp.post('/tournaments/<identifier>/validate')
@require_org('manage')
def validate_tournament(identifier):
    t=owned(Tournament,identifier);errors=validation_errors(t);return {'valid':not errors,'errors':errors,'defaults':t.rules,'review_required':True}

@bp.post('/tournaments/<identifier>/publish')
@require_org('manage')
def publish(identifier):
    db=get_db();t=owned(Tournament,identifier,True);data=body();visibility=data.get('visibility','private')
    if visibility not in ('private','public'):raise APIError('Choose public or private visibility.')
    errors=validation_errors(t)
    if errors:raise APIError('Resolve setup errors before publication.',400,details=errors)
    t.status='published' if t.status=='draft' else t.status;t.visibility=visibility
    audit('tournament.publish','tournament',t.id,after={'status':t.status,'visibility':visibility});db.commit();return serialize(t)

@bp.post('/tournaments/<identifier>/divisions')
@require_org('manage')
def create_division(identifier):
    t=owned(Tournament,identifier);data=body();d=Division(organization_id=t.organization_id,tournament_id=t.id,name=name(data.get('name')),category=data.get('category'),rules=validate_rules(data.get('rules',{}),partial=True),settings=data.get('settings',{}))
    get_db().add(d);get_db().flush();record_rules(d,'Initial division rules');audit('division.create','division',d.id,after=serialize(d));get_db().commit();return serialize(d),201

@bp.patch('/divisions/<identifier>')
@require_org('manage')
def update_division(identifier):
    d=owned(Division,identifier,True);data=body();before=serialize(d)
    if 'name' in data:d.name=name(data['name'])
    if 'category' in data:
        category=data['category']
        if category is not None and (not isinstance(category,str) or len(category)>100):raise APIError('Category must be text of at most 100 characters.')
        d.category=category.strip() or None if category is not None else None
    if 'rules' in data:
        value=validate_rules(data['rules'],partial=True);guard_ranking_change(d,value,data);d.rules=value;record_rules(d,data.get('reason','Explicit division rules revision'))
    if 'settings' in data:
        if not isinstance(data['settings'],dict):raise APIError('Settings must be an object.')
        d.settings={**d.settings,**data['settings']}
    audit('division.update','division',d.id,before=before,after=serialize(d));get_db().commit();return serialize(d)

@bp.post('/divisions/<identifier>/entries')
@require_org('manage')
def create_entry(identifier):
    db=get_db();d=owned(Division,identifier);data=body();team=owned(Team,data.get('team_id'))
    if team.archived:raise APIError('An archived team cannot enter a new event.')
    e=Entry(organization_id=d.organization_id,tournament_id=d.tournament_id,division_id=d.id,team_id=team.id,name=data.get('name') or team.name,color=team.color,
        seed=bounded_integer(data.get('seed',1),'Seed'),pool=str(data.get('pool') or '')[:40] or None,roster=data.get('roster',[]))
    db.add(e);db.flush();audit('entry.create','entry',e.id,after={'name':e.name,'division_id':d.id});db.commit();return serialize(e),201

@bp.patch('/entries/<identifier>')
@require_org('manage')
def update_entry(identifier):
    e=owned(Entry,identifier,True);data=body()
    if 'seed' in data:e.seed=bounded_integer(data['seed'],'Seed')
    if 'pool' in data:e.pool=str(data['pool'])[:40]
    if 'status' in data:
        if data['status'] not in ('active','withdrawn','archived'):raise APIError('Invalid participation status.')
        e.status=data['status']
    if 'name' in data:e.name=name(data['name'])
    if 'roster' in data:
        if not isinstance(data['roster'],list):raise APIError('Roster must be an event-specific list.')
        e.roster=data['roster']
    audit('entry.update','entry',e.id,after={'name':e.name,'status':e.status,'seed':e.seed,'pool':e.pool});get_db().commit();return serialize(e)

@bp.post('/divisions/<identifier>/stages')
@require_org('manage')
def create_stage(identifier):
    d=owned(Division,identifier);data=body();fmt=data.get('format','round_robin')
    if fmt not in FORMATS:raise APIError('Choose a supported V1 competition format.')
    if not isinstance(data.get('settings',{}),dict):raise APIError('Settings must be an object.')
    s=Stage(organization_id=d.organization_id,tournament_id=d.tournament_id,division_id=d.id,name=name(data.get('name')),format=fmt,
        position=bounded_integer(data.get('position',1),'Stage position',1,100),rules=validate_rules(data.get('rules',{}),True),settings=data.get('settings',{}))
    s.settings=validate_stage_options(s,s.settings)
    get_db().add(s);get_db().flush();record_rules(s,'Initial stage rules');audit('stage.create','stage',s.id,after=serialize(s));get_db().commit();return serialize(s),201

@bp.patch('/stages/<identifier>')
@require_org('manage')
def update_stage(identifier):
    s=owned(Stage,identifier,True);data=body()
    if 'name' in data:s.name=name(data['name'])
    fixtures_exist=bool(get_db().scalar(select(Match.id).where(Match.stage_id==s.id).limit(1)))
    for key in ('format','position'):
        if key not in data:continue
        value=data[key] if key=='format' else bounded_integer(data[key],'Stage position',1,100)
        if key=='format' and value not in FORMATS:raise APIError('Choose a supported V1 competition format.')
        if fixtures_exist and value!=getattr(s,key):raise APIError('Stage format and order cannot change after fixtures are generated. Create a separate stage instead.',409)
        setattr(s,key,value)
    if 'rules' in data:
        value=validate_rules(data['rules'],True);guard_ranking_change(s,value,data);s.rules=value;record_rules(s,data.get('reason','Explicit stage rules revision'))
    if 'settings' in data:
        if not isinstance(data['settings'],dict):raise APIError('Settings must be an object.')
        values=validate_stage_options(s,{**(s.settings or {}),**data['settings']})
        if fixtures_exist and any(values.get(key)!=(s.settings or {}).get(key) for key in ('source_stage_id','qualifier_count','qualify_per_pool','wildcard_places','pool_count','repeat')):
            raise APIError('Qualification and encounter structure cannot change after fixtures are generated. Review results or create a separate stage.',409)
        s.settings=values
    audit('stage.update','stage',s.id,after=serialize(s));get_db().commit();return serialize(s)

def validate_stage_options(stage,settings):
    values=dict(settings)
    limits={'repeat':(1,10),'pool_count':(1,16),'qualify_per_pool':(1,128),'qualifier_count':(2,128),
        'wildcard_places':(0,128),'interval_minutes':(1,10080),'duration_minutes':(1,1440)}
    for key,(minimum,maximum) in limits.items():
        if key in values:values[key]=bounded_integer(values[key],key.replace('_',' ').capitalize(),minimum,maximum)
    source_id=values.get('source_stage_id')
    if source_id:
        source=owned(Stage,source_id)
        if source.division_id!=stage.division_id or source.tournament_id!=stage.tournament_id or source.position>=stage.position or source.id==stage.id or stage.format!='single_elimination' or source.format not in ('round_robin','league','pools_knockout'):
            raise APIError('Choose an earlier league, round-robin or pool stage in this division as the knockout qualification source.')
    return values

def schedule_conflicts(m,court_id=None):
    if not m.planned_at:return []
    db=get_db();t=db.get(Tournament,m.tournament_id);buffer=int((t.settings or {}).get('buffer_minutes',0));rest=int((t.settings or {}).get('rest_minutes',0));errors=[]
    end=m.planned_at+timedelta(minutes=m.planned_duration+buffer)
    q=select(Match).where(Match.organization_id==m.organization_id,Match.id!=m.id,Match.planned_at.is_not(None),Match.status.not_in(['cancelled','abandoned']),Match.planned_at<end+timedelta(minutes=rest))
    team_ids=set(db.scalars(select(Entry.team_id).where(Entry.id.in_([m.entry_a_id,m.entry_b_id]))))
    for other in db.scalars(q):
        other_end=other.planned_at+timedelta(minutes=other.planned_duration+buffer)
        if m.planned_at>=other_end+timedelta(minutes=rest):continue
        other_teams=set(db.scalars(select(Entry.team_id).where(Entry.id.in_([other.entry_a_id,other.entry_b_id]))))
        if team_ids & other_teams:errors.append(f'Team overlap or rest conflict with game {other.id}.')
        if m.referee_name and m.referee_name.strip().casefold()==(other.referee_name or '').strip().casefold():errors.append(f'Referee overlap with game {other.id}.')
        if court_id:
            assigned=assignment(other)
            if assigned and assigned.court_id==court_id and m.planned_at<other_end:errors.append(f'Court booking overlaps game {other.id}.')
    for source_id in (m.source_a_id,m.source_b_id):
        if source_id:
            source=db.get(Match,source_id)
            if source.planned_at and m.planned_at<source.planned_at+timedelta(minutes=source.planned_duration+rest):errors.append(f'Qualification dependency {source.id} finishes after this game.')
    return errors

def lock_schedule(organization_id):
    get_db().execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':'schedule:'+str(organization_id)})

@bp.post('/stages/<identifier>/generate')
@require_org('schedule')
def generate(identifier):
    db=get_db();stage=owned(Stage,identifier,True);data=body();entries=list(db.scalars(select(Entry).where(Entry.division_id==stage.division_id,Entry.status=='active').order_by(Entry.seed,Entry.created_at)))
    if not data.get('preview',True):lock_schedule(stage.organization_id)
    if len(entries)<2:raise APIError('Add at least two active entries before generating fixtures.')
    if len(entries)>128:raise APIError('V1 generation supports up to 128 entries per stage.')
    if db.scalar(select(Match.id).where(Match.stage_id==stage.id)):raise APIError('This stage already has fixtures. Review and revise its schedule instead of regenerating it.',409)
    settings=dict(stage.settings or {})
    repeat=bounded_integer(data.get('repeat',settings.get('repeat',1)),'Repeat encounters',1,10)
    start=instant(data.get('start_at'));interval=bounded_integer(data.get('interval_minutes',settings.get('interval_minutes',75)),'Fixture interval',1,10080)
    duration=bounded_integer(data.get('duration_minutes',settings.get('duration_minutes',60)),'Match duration',1,1440)
    pools={};descriptors=[];playoff_descriptors=[];qualify=0;wildcards=0
    if stage.format in ('round_robin','league'):descriptors=round_robin([e.id for e in entries],repeat)
    elif stage.format=='single_elimination':
        source_id=settings.get('source_stage_id')
        if source_id:
            source=db.get(Stage,source_id)
            if not source or source.division_id!=stage.division_id or source.tournament_id!=stage.tournament_id or source.position>=stage.position or source.format not in ('league','round_robin'):
                raise APIError('Choose an earlier league or round-robin stage in this division for season playoff qualification. Pool playoffs are generated with the pool stage.')
            count=bounded_integer(data.get('qualifier_count',settings.get('qualifier_count',min(4,len(entries)))),'Season playoff qualifiers',2,len(entries))
            settings['qualifier_count']=count
            descriptors=bracket([f'qualifier:{rank}:season' for rank in range(1,count+1)])
        else:descriptors=bracket([e.id for e in entries])
    elif stage.format=='pools_knockout':
        count=bounded_integer(data.get('pool_count',settings.get('pool_count',2)),'Pool count',1,min(16,len(entries)//2))
        qualify=bounded_integer(data.get('qualify_per_pool',settings.get('qualify_per_pool',2)),'Qualifiers per pool',1,len(entries)//count)
        labels=sorted(set(e.pool for e in entries if e.pool))
        if len(labels)==count and all(e.pool for e in entries):
            for label in labels:pools[label]=[e.id for e in entries if e.pool==label]
        else:
            for i,e in enumerate(entries):pools.setdefault(chr(65+i%count),[]).append(e.id)
        if any(len(ids)<qualify for ids in pools.values()):raise APIError('Each pool needs enough teams for its qualifying places.')
        for label,ids in pools.items():descriptors.extend([{**x,'pool':label} for x in round_robin(ids,repeat)])
        wildcards=bounded_integer(data.get('wildcard_places',settings.get('wildcard_places',0)),'Wildcard qualifying places',0,len(entries)-count*qualify)
        placeholders=[f'qualifier:{rank}:{label}' for rank in range(1,qualify+1) for label in pools]+[f'wildcard:{i+1}' for i in range(wildcards)]
        playoff_descriptors=bracket(placeholders)
    for i,item in enumerate(descriptors):item['planned_at']=iso(start+timedelta(minutes=i*interval)) if start else None
    summary={'format':stage.format,'fixtures':descriptors,'playoff_fixtures':playoff_descriptors,'pools':pools,'total':len(descriptors)+len(playoff_descriptors),'review_required':True,'warnings':[]}
    if data.get('preview',True):return summary
    t=db.get(Tournament,stage.tournament_id)
    stage.settings={**settings,'repeat':repeat,'interval_minutes':interval,'duration_minutes':duration,
        **({'pool_count':count,'qualify_per_pool':qualify,'wildcard_places':wildcards} if stage.format=='pools_knockout' else {})}
    created=[]
    for i,item in enumerate(descriptors):
        is_qualifying=stage.format=='single_elimination' and bool(settings.get('source_stage_id'))
        m=Match(organization_id=stage.organization_id,tournament_id=stage.tournament_id,division_id=stage.division_id,stage_id=stage.id,
            entry_a_id=None if is_qualifying else item['a'],entry_b_id=None if is_qualifying else item['b'],round_number=item['round'],pool=item.get('pool'),planned_at=instant(item['planned_at']),planned_duration=duration,state={},
            settings={'qualifier_a':item.get('a'),'qualifier_b':item.get('b'),'bye':bool(item.get('bye'))} if is_qualifying else {})
        db.add(m);db.flush();created.append(m)
        if item.get('source_a') is not None:m.source_a_id=created[item['source_a']].id
        if item.get('source_b') is not None:m.source_b_id=created[item['source_b']].id
        if not is_qualifying and item.get('bye') and bool(m.entry_a_id)!=bool(m.entry_b_id):
            m.status='ended';m.outcome='bye';m.result_status='finalized';m.winner_id=m.entry_a_id or m.entry_b_id
        if m.planned_at:
            local=m.planned_at.astimezone(ZoneInfo(t.timezone)).date()
            if not t.start_date<=local<=t.end_date:raise APIError('Generated schedule extends beyond event dates. Adjust the start/interval or event dates.')
    for label,ids in pools.items():
        for eid in ids:db.get(Entry,eid).pool=label
    if playoff_descriptors:
        playoff=Stage(organization_id=stage.organization_id,tournament_id=stage.tournament_id,division_id=stage.division_id,name='Playoffs',format='single_elimination',position=stage.position+1,rules={},settings={'source_stage_id':stage.id,'qualify_per_pool':qualify,'wildcard_places':wildcards})
        db.add(playoff);db.flush();playoff_matches=[]
        for item in playoff_descriptors:
            m=Match(organization_id=stage.organization_id,tournament_id=stage.tournament_id,division_id=stage.division_id,stage_id=playoff.id,round_number=item['round'],state={},settings={'qualifier_a':item.get('a'),'qualifier_b':item.get('b'),'bye':bool(item.get('bye'))})
            db.add(m);db.flush();playoff_matches.append(m)
            if item.get('source_a') is not None:m.source_a_id=playoff_matches[item['source_a']].id
            if item.get('source_b') is not None:m.source_b_id=playoff_matches[item['source_b']].id
    db.flush()
    for m in created:
        if m.outcome=='bye':propagate_winner(m)
    conflicts=[c for m in created for c in schedule_conflicts(m)]
    if conflicts:raise APIError('Generated fixtures have conflicts. Adjust the interval or rules and review again.',400,details=conflicts)
    if stage.format=='single_elimination' and settings.get('source_stage_id'):
        previous=list(db.scalars(select(Match).where(Match.stage_id==settings['source_stage_id'])))
        if previous and all(game.result_status=='finalized' or game.status=='cancelled' for game in previous):advance_results(previous[-1])
    audit('schedule.generate','stage',stage.id,after={'count':len(created),'pools':pools,'format':stage.format});db.commit();return {'created':len(created),'matches':[match_json(m) for m in created],**summary},201

@bp.route('/matches',methods=['GET','POST'])
@require_org('read')
def matches():
    db=get_db()
    if request.method=='GET':
        q=select(Match).where(Match.organization_id==g.organization.id)
        ids=scoped_tournament_ids()
        if ids is not None:q=q.where(Match.tournament_id.in_(ids))
        for key in ('tournament_id','division_id','stage_id','status'):
            if request.args.get(key):q=q.where(getattr(Match,key)==request.args[key])
        items=list(db.scalars(q.order_by(Match.planned_at).limit(500)));return {'items':[match_json(m) for m in items],'total':len(items)}
    if g.membership.role not in ('admin','manager','scheduler'):raise APIError('Scheduling permission is required.',403)
    data=body();stage=owned(Stage,data.get('stage_id'));lock_schedule(stage.organization_id)
    a=owned(Entry,data.get('entry_a_id'));b=owned(Entry,data.get('entry_b_id'))
    if a.division_id!=stage.division_id or b.division_id!=stage.division_id or a.id==b.id:raise APIError('Choose two different entries in this division.')
    m=Match(organization_id=stage.organization_id,tournament_id=stage.tournament_id,division_id=stage.division_id,stage_id=stage.id,entry_a_id=a.id,entry_b_id=b.id,
        planned_at=instant(data.get('planned_at')),planned_duration=bounded_integer(data.get('planned_duration',60),'Duration',1,1440),
        round_number=bounded_integer(data.get('round_number',1),'Fixture round',1,10000),referee_name=data.get('referee_name'),state={})
    db.add(m);db.flush();conflicts=schedule_conflicts(m)
    if conflicts:raise APIError('Scheduling conflicts need resolution.',400,details=conflicts)
    audit('match.create','match',m.id,after={'stage_id':stage.id,'planned_at':iso(m.planned_at)});db.commit();return match_json(m),201

@bp.route('/matches/<identifier>',methods=['GET','PATCH'])
@require_org('read')
def match(identifier):
    db=get_db();m=owned(Match,identifier,request.method=='PATCH')
    if request.method=='GET':return match_json(m)
    if g.membership.role not in ('admin','manager','scheduler'):raise APIError('Scheduling permission is required.',403)
    lock_schedule(m.organization_id)
    data=body();before=match_json(m)
    for key in ('planned_at','planned_duration','referee_name'):
        if key in data:setattr(m,key,instant(data[key]) if key=='planned_at' else bounded_integer(data[key],'Duration',1,1440) if key=='planned_duration' else str(data[key])[:120])
    if 'rules' in data:
        if g.membership.role not in ('admin','manager'):raise APIError('Only a manager can override scoring rules.',403)
        if m.rule_snapshot:raise APIError('Rules are frozen for this started game.',409)
        if not data.get('reason'):raise APIError('A match rule override requires a reason.')
        m.rules=validate_rules(data['rules'],True)
    if 'status' in data:
        if m.status in ('in_progress','paused','ended'):raise APIError('Use explicit lifecycle/outcome actions for a started game.',409)
        if data['status'] not in ('scheduled','postponed'):raise APIError('Only scheduled or postponed can be set through schedule revisions.')
        m.status=data['status']
    conflicts=schedule_conflicts(m)
    if conflicts:raise APIError('Resolve the scheduling conflicts.',400,details=conflicts)
    m.version+=1;audit('schedule.revise','match',m.id,before=before,after=match_json(m),reason=data.get('reason'));emit(m,'schedule.changed');db.commit();return match_json(m)

@bp.post('/matches/<identifier>/court')
@require_org('schedule')
def court(identifier):
    db=get_db();hint=owned(Match,identifier)
    # Venue links and result workflows use tournament -> match lock order.
    # Lock the association owner first so detachment cannot race reservation.
    db.scalar(select(Tournament).where(Tournament.id==hint.tournament_id,Tournament.organization_id==g.organization.id).with_for_update())
    m=owned(Match,identifier,True);data=body();cid=valid_id(data.get('court_id'))
    lock_schedule(m.organization_id)
    conflicts=schedule_conflicts(m,cid)
    if conflicts:raise APIError('Resolve schedule/court conflicts before reserving.',400,details=conflicts)
    assign_court(m,cid,g.user.id,data.get('reason'));db.commit();return match_json(m)

@bp.post('/matches/<identifier>/release')
@require_org('schedule')
def release(identifier):
    m=owned(Match,identifier,True);release_court(m,body().get('reason'));m.version+=1;emit(m,'court.released');get_db().commit();return match_json(m)

@bp.post('/matches/<identifier>/qr')
@require_org('manage')
def qr(identifier):
    db=get_db();m=owned(Match,identifier,True)
    if m.status not in ('ready','in_progress','paused'):raise APIError('Assign a court before revealing game access; closed games cannot admit a scorer.',409)
    raw=secrets.token_urlsafe(40);expiry=now()+timedelta(minutes=int((g.organization.settings or {}).get('qr_admission_minutes',15)))
    grant=Grant(organization_id=m.organization_id,match_id=m.id,token_hash=hashlib.sha256(raw.encode()).hexdigest(),issued_by=g.user.id,expires_at=expiry)
    db.add(grant);audit('qr.issue','match',m.id,after={'expires_at':iso(expiry)})
    url=current_app.config.get('PUBLIC_BASE_URL',request.host_url.rstrip('/'))+'/referee#'+raw
    img=qrcode.make(url,image_factory=qrcode.image.svg.SvgPathImage,box_size=8,border=3);buff=io.BytesIO();img.save(buff)
    db.commit();return {'url':url,'qr_svg':buff.getvalue().decode(),'expires_at':iso(expiry),'display_seconds':10}

@bp.post('/matches/<identifier>/revoke')
@require_org('manage')
def revoke(identifier):
    db=get_db();m=owned(Match,identifier,True)
    db.execute(update(Grant).where(Grant.match_id==m.id,Grant.revoked_at.is_(None)).values(revoked_at=now()))
    db.execute(update(ScorerSession).where(ScorerSession.match_id==m.id,ScorerSession.revoked_at.is_(None)).values(revoked_at=now()))
    m.generation+=1;m.version+=1;audit('referee.revoke','match',m.id);emit(m,'scorer.revoked');db.commit();return match_json(m)

def organizer_event(m,kind,reason=None):
    payload={'after':deepcopy(m.state),'lifecycle':{'status':m.status,'actual_start':iso(m.actual_start),'actual_end':iso(m.actual_end),'winner_id':m.winner_id,'outcome':m.outcome},'reason':reason}
    get_db().add(MatchEvent(organization_id=m.organization_id,match_id=m.id,sequence=m.version,client_action_id=str(__import__('uuid').uuid4()),type=kind,actor_id=g.user.id,payload=payload,expected_version=m.version-1,new_version=m.version,receipt={'version':m.version}))

def propagate_winner(m):
    db=get_db()
    for downstream in db.scalars(select(Match).where(or_(Match.source_a_id==m.id,Match.source_b_id==m.id)).order_by(Match.id).with_for_update()):
        for side in ('a','b'):
            if getattr(downstream,'source_'+side+'_id')!=m.id:continue
            current=getattr(downstream,'entry_'+side+'_id')
            if current==m.winner_id:continue
            if downstream.status in ('in_progress','paused','ended'):
                downstream.settings={**(downstream.settings or {}),'adjudication_required':True,'qualifier_change':{'source':m.id,'proposed':m.winner_id}}
                audit('qualifier.blocked','match',downstream.id,reason='Downstream game already started; played teams preserved')
            else:
                setattr(downstream,'entry_'+side+'_id',m.winner_id);downstream.version+=1
                audit('qualifier.updated','match',downstream.id,after={'side':side,'entry_id':m.winner_id});emit(downstream,'qualifier.changed')

def advance_results(m):
    db=get_db();t=db.scalar(select(Tournament).where(Tournament.id==m.tournament_id).with_for_update());t.standings_generation+=1
    if m.winner_id:propagate_winner(m)
    stage=db.get(Stage,m.stage_id)
    if stage.format in ('pools_knockout','league','round_robin'):
        games=list(db.scalars(select(Match).where(Match.stage_id==stage.id)))
        if games and all(game.result_status=='finalized' or game.status=='cancelled' for game in games):
            division=db.get(Division,m.division_id);is_pool=stage.format=='pools_knockout';pools=sorted(set(game.pool for game in games if game.pool)) if is_pool else ['season']
            for playoff in db.scalars(select(Stage).where(Stage.division_id==division.id,Stage.format=='single_elimination')):
                settings=playoff.settings or {}
                if settings.get('source_stage_id',settings.get('qualification_source_stage'))!=stage.id:continue
                settings={k:v for k,v in settings.items() if k!='qualification_blocked'}
                playoff.settings=settings
                count=int(settings.get('qualify_per_pool',settings.get('qualifiers_per_pool',2))) if is_pool else int(settings.get('qualifier_count',4))
                rankings={pool:standings(division,stage.id,pool=pool if is_pool else None)['rows'] for pool in pools}
                qualifiers={}
                for pool,rows in rankings.items():
                    for rank,row in enumerate(rows[:count],1):
                        if row['tied']:playoff.settings={**settings,'qualification_blocked':'Resolve pool ranking ties before advancement.'};continue
                        qualifiers[f'qualifier:{rank}:{pool}']=row['entry_id']
                wildcard_count=int(settings.get('wildcard_places',0))
                if wildcard_count:
                    candidates=[{**row,'source_pool':pool} for pool,rows in rankings.items() for row in rows[count:]]
                    compared=compare_pools(candidates,effective_rules(t,division,stage),(division.settings or {}).get('tie_resolution'))
                    for index,row in enumerate(compared[:wildcard_count],1):
                        if row['comparison_tied'] or row['tied']:playoff.settings={**playoff.settings,'qualification_blocked':'Resolve cross-pool qualification ties before advancement.'}
                        else:qualifiers[f'wildcard:{index}']=row['entry_id']
                first=list(db.scalars(select(Match).where(Match.stage_id==playoff.id,Match.round_number==1).order_by(Match.id).with_for_update()))
                ordered=[qualifiers.get(f'qualifier:{rank}:{pool}') for rank in range(1,count+1) for pool in pools]
                for index,game in enumerate(first):
                    for side in ('a','b'):
                        participant_key='qualifier_'+side;has_qualifier_key=participant_key in (game.settings or {})
                        key=(game.settings or {}).get(participant_key)
                        proposed=qualifiers.get(key) if has_qualifier_key else (ordered[index*2+(side=='b')] if index*2+(side=='b')<len(ordered) else None)
                        if not proposed and not has_qualifier_key:continue
                        if getattr(game,'entry_'+side+'_id')==proposed:continue
                        if game.status in ('in_progress','paused','ended') and getattr(game,'entry_'+side+'_id')!=proposed:
                            game.settings={**game.settings,'adjudication_required':True};audit('qualifier.blocked','match',game.id,reason='Started downstream match')
                        elif game.status not in ('in_progress','paused','ended'):
                            setattr(game,'entry_'+side+'_id',proposed);game.version+=1;emit(game,'qualifier.changed')
                    if (game.settings or {}).get('bye') and bool(game.entry_a_id)!=bool(game.entry_b_id) and game.status=='scheduled':
                        game.status='ended';game.outcome='bye';game.result_status='finalized';game.winner_id=game.entry_a_id or game.entry_b_id;propagate_winner(game)

def locked_result(identifier):
    db=get_db();hint=owned(Match,identifier);db.scalar(select(Tournament).where(Tournament.id==hint.tournament_id).with_for_update())
    return owned(Match,identifier,True)

@bp.post('/matches/<identifier>/approve')
@require_org('results')
def approve(identifier):
    db=get_db();m=locked_result(identifier)
    if m.status!='ended' or m.result_status!='submitted':raise APIError('Only a submitted ended result can be approved.',409)
    stage=db.get(Stage,m.stage_id)
    if stage.format=='single_elimination' and not m.winner_id:raise APIError('Resolve the knockout winner explicitly before finalization.',409,'winner_required')
    previous=db.scalar(select(ResultRevision).where(ResultRevision.match_id==m.id,ResultRevision.revision==m.result_revision))
    m.result_revision+=1;m.result_status='finalized';m.version+=1
    db.add(ResultRevision(organization_id=m.organization_id,match_id=m.id,revision=m.result_revision,sets=m.state.get('sets',[]),winner_id=m.winner_id,outcome=m.outcome,status='finalized',approved_by=g.user.id,approved_at=now(),predecessor_id=previous.id if previous else None,reason='Organizer approval'))
    organizer_event(m,'result_approve','Organizer approval');advance_results(m);audit('result.approve','match',m.id,after={'revision':m.result_revision});emit(m,'result.finalized');db.commit();return match_json(m)

@bp.post('/matches/<identifier>/correct')
@require_org('results')
def correct_result(identifier):
    db=get_db();m=locked_result(identifier);data=body()
    if m.status!='ended':raise APIError('Use referee corrections for an open game; this workflow revises ended results.',409)
    reason=name(data.get('reason'),'Correction reason',2000);sets=data.get('sets')
    if not isinstance(sets,list) or not sets or len(sets)>9:raise APIError('Provide a valid set breakdown.')
    rules=m.rule_snapshot or effective_rules(db.get(Tournament,m.tournament_id),db.get(Division,m.division_id),db.get(Stage,m.stage_id),m)
    validated=[]
    for index,s in enumerate(sets):
        if not isinstance(s,dict):raise APIError('Each set needs a and b scores.')
        a=bounded_integer(s.get('a'),'Team A score',0,1000);b=bounded_integer(s.get('b'),'Team B score',0,1000)
        if rules['cap'] and max(a,b)>rules['cap']:raise APIError('Scores cannot exceed the hard cap.')
        winner=set_winner(a,b,rules,index)
        if not winner:raise APIError('Every corrected set must be complete under the frozen rules.')
        validated.append({'a':a,'b':b,'complete':True,'winner':winner})
        if match_winner({'sets':validated},rules) and index<len(sets)-1:raise APIError('Extra sets cannot follow the match winning condition.')
    winning=match_winner({'sets':validated},rules)
    if not winning:raise APIError('The corrected sets do not complete this match.')
    winner=m.entry_a_id if winning=='a' else m.entry_b_id if winning=='b' else None
    downstream=[{'id':x.id,'status':x.status,'blocked':x.status in ('in_progress','paused','ended'),'current_a':x.entry_a_id,'current_b':x.entry_b_id} for x in db.scalars(select(Match).where(or_(Match.source_a_id==m.id,Match.source_b_id==m.id)))]
    impact={'sets':validated,'winner_id':winner,'downstream':downstream,'standings_will_recalculate':m.result_status=='finalized','requires_adjudication':any(x['blocked'] for x in downstream) and winner!=m.winner_id}
    if data.get('preview',True):return {'preview':impact,'requires_confirmation':True}
    before=match_json(m);previous=db.scalar(select(ResultRevision).where(ResultRevision.match_id==m.id,ResultRevision.revision==m.result_revision))
    m.state={**m.state,'sets':validated,'set_index':len(validated)-1};m.winner_id=winner;m.outcome='draw' if winning=='draw' else 'normal';m.result_revision+=1;m.version+=1
    db.add(ResultRevision(organization_id=m.organization_id,match_id=m.id,revision=m.result_revision,sets=validated,winner_id=winner,outcome=m.outcome,status=m.result_status,approved_by=g.user.id if m.result_status=='finalized' else None,approved_at=now() if m.result_status=='finalized' else None,reason=reason,predecessor_id=previous.id if previous else None))
    organizer_event(m,'result_correct',reason)
    if m.result_status=='finalized':advance_results(m)
    audit('result.correct','match',m.id,before=before,after=match_json(m),reason=reason);emit(m,'result.corrected');db.commit();return {'match':match_json(m),'impact':impact}

@bp.post('/matches/<identifier>/resolve-winner')
@require_org('results')
def resolve_winner(identifier):
    db=get_db();m=locked_result(identifier);data=body();reason=name(data.get('reason'),'Adjudication reason',2000)
    winner=data.get('winner_id')
    if m.status!='ended' or winner not in (m.entry_a_id,m.entry_b_id):raise APIError('Choose a played participant on an ended match.')
    previous=db.scalar(select(ResultRevision).where(ResultRevision.match_id==m.id,ResultRevision.revision==m.result_revision))
    m.winner_id=winner;m.outcome='adjudicated';m.result_revision+=1;m.version+=1
    db.add(ResultRevision(organization_id=m.organization_id,match_id=m.id,revision=m.result_revision,sets=m.state.get('sets',[]),winner_id=winner,outcome=m.outcome,status=m.result_status,approved_by=g.user.id if m.result_status=='finalized' else None,approved_at=now() if m.result_status=='finalized' else None,reason=reason,predecessor_id=previous.id if previous else None))
    organizer_event(m,'winner_resolved',reason)
    if m.result_status=='finalized':advance_results(m)
    audit('result.winner_resolved','match',m.id,after={'winner_id':winner},reason=reason);emit(m,'result.corrected');db.commit();return match_json(m)

@bp.post('/matches/<identifier>/outcome')
@require_org('results')
def exceptional(identifier):
    db=get_db();m=locked_result(identifier);data=body();outcome=data.get('outcome');reason=name(data.get('reason'),'Reason',2000)
    if outcome not in ('forfeit','abandoned','cancelled'):raise APIError('Choose forfeit, abandoned or cancelled.')
    if m.result_status=='finalized':raise APIError('Use an audited result revision for finalized games.',409)
    winner_id=data.get('winner_id')
    if outcome=='forfeit' and winner_id not in (m.entry_a_id,m.entry_b_id):raise APIError('A forfeit requires the winning entry.')
    state=deepcopy(m.state);stop_clock(state);m.state=state;release_court(m,reason,ending=True)
    m.status='ended' if outcome=='forfeit' else outcome;m.actual_end=now();m.outcome=outcome;m.winner_id=winner_id if outcome=='forfeit' else None;m.result_status='submitted' if outcome=='forfeit' else 'excluded';m.result_revision+=1;m.version+=1
    db.add(ResultRevision(organization_id=m.organization_id,match_id=m.id,revision=m.result_revision,sets=state.get('sets',[]),winner_id=m.winner_id,outcome=outcome,status=m.result_status,reason=reason))
    organizer_event(m,'exceptional_outcome',reason)
    audit('match.exceptional','match',m.id,after={'outcome':outcome,'winner_id':m.winner_id},reason=reason);emit(m,'match.closed');db.commit();return match_json(m)

@bp.get('/divisions/<identifier>/standings')
@require_org('read')
def division_standings(identifier):
    d=owned(Division,identifier);stage_id=request.args.get('stage_id')
    if stage_id:
        stage=owned(Stage,stage_id)
        if stage.division_id!=d.id:raise APIError('Stage must belong to the selected division.')
    return standings(d,stage_id,request.args.get('provisional')=='true')

@bp.post('/divisions/<identifier>/resolve-tie')
@require_org('results')
def resolve_tie(identifier):
    d=owned(Division,identifier,True);data=body();reason=name(data.get('reason'),'Reason',2000);order=data.get('order')
    if not isinstance(order,list) or len(set(order))!=len(order):raise APIError('Supply a unique ordered list of tied entries.')
    for eid in order:
        e=owned(Entry,eid)
        if e.division_id!=d.id:raise APIError('Tie resolution entries must belong to this division.')
    d.settings={**d.settings,'tie_resolution':{'order':order,'reason':reason}}
    d.rules={**d.rules,'tie_breaks':[c for c in (d.rules.get('tie_breaks') or DEFAULT_RULES['tie_breaks']) if c!='manual']+['manual']}
    record_rules(d,reason)
    db=get_db();t=db.get(Tournament,d.tournament_id);t.standings_generation+=1
    for stage in db.scalars(select(Stage).where(Stage.division_id==d.id,Stage.format.in_(['pools_knockout','league','round_robin']))):
        last=db.scalar(select(Match).where(Match.stage_id==stage.id,Match.result_status=='finalized').limit(1))
        if last:advance_results(last)
    audit('ranking.resolve','division',d.id,after={'order':order},reason=reason);db.commit();return standings(d)

@bp.get('/public/<slug>')
def public_detail(slug):return tournament_detail(public_tournament(slug),public=True)

@bp.get('/public/<slug>/events')
def public_events(slug):
    # Bounded polling fallback is authoritative and survives process restarts;
    # all outbox rows remain durable for future SSE/job publication adapters.
    t=public_tournament(slug);since=request.args.get('since')
    q=select(Outbox).where(Outbox.tournament_id==t.id).order_by(Outbox.created_at.desc()).limit(100)
    if since:
        try:q=q.where(Outbox.created_at>instant(since))
        except APIError:raise
    events=list(get_db().scalars(q))
    return {'events':[{'id':e.id,'kind':e.kind,'version':e.version,'match_id':e.match_id,'created_at':iso(e.created_at)} for e in reversed(events)],'updated_at':iso(now()),'visibility':'public'}
