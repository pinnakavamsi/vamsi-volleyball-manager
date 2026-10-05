"""Durable, fenced, idempotent delegated scoring transactions."""
import hashlib,secrets,uuid
from copy import deepcopy
from datetime import datetime,timedelta
from functools import wraps
from flask import Blueprint,g,session,request,current_app
from sqlalchemy import select,update
from .models import (Grant,ScorerSession,Match,MatchEvent,Tournament,Division,Stage,Organization,ResultRevision)
from .common import APIError,now,body,audit,emit,iso
from .db import get_db,set_scope
from .services.rules import effective_rules,set_winner,match_winner
from .services.courts import start_court,release_court
bp=Blueprint('scoring',__name__,url_prefix='/api/v1/referee')

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def initial_state(rules):return {'sets':[{'a':0,'b':0,'complete':False,'winner':None}],'set_index':0,'serve':'a','timeouts':{'a':rules['timeouts'],'b':rules['timeouts']},'clock_seconds':0,'clock_anchor':None}
def elapsed(match):
    state=match.state or {};seconds=state.get('clock_seconds',0)
    if state.get('clock_anchor'):
        seconds+=max(0,(now()-datetime.fromisoformat(state['clock_anchor'])).total_seconds())
    return int(seconds)
def stop_clock(state):
    if state.get('clock_anchor'):
        state['clock_seconds']=state.get('clock_seconds',0)+max(0,(now()-datetime.fromisoformat(state['clock_anchor'])).total_seconds())
    state['clock_anchor']=None

def require_referee(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        db=get_db();raw=session.get('referee_token')
        row=db.scalar(select(ScorerSession).where(ScorerSession.token_hash==digest(raw or '')))
        if not row or row.revoked_at or row.expires_at<now():raise APIError('Scoring access expired or was revoked. Re-scan the game QR.',401,'referee_access')
        org=db.get(Organization,row.organization_id)
        if not org or not org.active:raise APIError('Organization access is unavailable.',403)
        set_scope(org.id);g.scorer=row;g.organization=org
        return fn(*args,**kwargs)
    return wrapped

def referee_match(lock=False):
    q=select(Match).where(Match.id==g.scorer.match_id,Match.organization_id==g.scorer.organization_id)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    match=get_db().scalar(q)
    if not match:raise APIError('Game is unavailable.',404)
    return match

def response(match,**extra):
    from .competition import match_json
    return {'match':match_json(match), 'session':{'generation':g.scorer.generation,'name':g.scorer.name},**extra}

@bp.post('/exchange')
def exchange():
    data=body();token=data.get('token','')
    if not isinstance(token,str) or len(token)>200:raise APIError('Invalid game QR.',401)
    db=get_db();grant=db.scalar(select(Grant).where(Grant.token_hash==digest(token)))
    if not grant or grant.revoked_at or grant.expires_at<now():raise APIError('This admission QR expired. Ask the organizer to show it again.',401,'grant_expired')
    org=db.get(Organization,grant.organization_id)
    if not org or not org.active:raise APIError('Organization access is unavailable.',403)
    set_scope(org.id);g.organization=org
    match=db.scalar(select(Match).where(Match.id==grant.match_id).with_for_update())
    db.refresh(grant,with_for_update=True)
    if grant.revoked_at or grant.expires_at<now():raise APIError('This admission grant was revoked or expired. Ask the organizer to reveal access again.',401,'grant_expired')
    if match.status not in ('ready','in_progress','paused'):raise APIError('QR admission is available for prepared or ongoing games only.',409)
    active=db.scalar(select(ScorerSession).where(ScorerSession.match_id==match.id,ScorerSession.revoked_at.is_(None),ScorerSession.generation>=0))
    generation=-1
    if not active and match.status=='ready':
        match.generation+=1;generation=match.generation
    raw=secrets.token_urlsafe(40)
    row=ScorerSession(organization_id=org.id,match_id=match.id,grant_id=grant.id,token_hash=digest(raw),generation=generation,
        expires_at=now()+timedelta(hours=24),name=str(data.get('name','Referee'))[:120])
    db.add(row);db.flush();g.scorer=row
    session['referee_token']=raw;session['referee_session_id']=row.id
    from .auth import csrf_token
    csrf=csrf_token()
    audit('referee.admitted','match',match.id,after={'session_id':row.id,'generation':generation})
    db.commit();return response(match,csrf_token=csrf)

@bp.get('/state')
@require_referee
def state():
    match=referee_match();g.scorer.last_seen_at=now();result=response(match);get_db().commit();return result

@bp.post('/resume')
@require_referee
def takeover():
    data=body()
    if data.get('takeover') is not True:raise APIError('Confirm Resume and take scoring control.')
    db=get_db();match=referee_match(lock=True)
    db.refresh(g.scorer)
    if g.scorer.revoked_at or g.scorer.expires_at<now():raise APIError('Scoring access was revoked while this request waited. Re-scan the game QR.',401,'referee_access')
    g.scorer.last_seen_at=now()
    if match.status not in ('ready','in_progress','paused'):raise APIError('This game is closed; ask an organizer to review the result.',409)
    db.execute(update(ScorerSession).where(ScorerSession.match_id==match.id,ScorerSession.id!=g.scorer.id,ScorerSession.revoked_at.is_(None)).values(revoked_at=now()))
    db.flush();match.generation+=1;match.version+=1;g.scorer.generation=match.generation;g.scorer.expires_at=now()+timedelta(hours=24)
    audit('scorer.takeover','match',match.id,after={'generation':match.generation,'session_id':g.scorer.id},reason='Explicit Resume and take scoring control')
    emit(match,'scorer.changed');db.commit();return response(match)

@bp.post('/renew')
@require_referee
def renew():
    match=referee_match(lock=True)
    get_db().refresh(g.scorer)
    if g.scorer.revoked_at:raise APIError('Scoring access was revoked.',401)
    g.scorer.last_seen_at=now()
    if match.status not in ('ready','in_progress','paused'):raise APIError('Game is closed.',409)
    g.scorer.expires_at=now()+timedelta(hours=24);get_db().commit();return {'renewed':True,'expires_at':iso(g.scorer.expires_at)}

@bp.post('/exit')
@require_referee
def exit_referee():
    referee_match(lock=True);get_db().refresh(g.scorer)
    g.scorer.revoked_at=now();audit('referee.exit','match',g.scorer.match_id);get_db().commit()
    session.pop('referee_token',None);session.pop('referee_session_id',None)
    return {'exited':True}

def check_number(value,label):
    if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=1000:raise APIError(f'{label} must be a nonnegative integer no greater than 1000.')
    return value

def mutate(match,kind,payload):
    db=get_db();rules=match.rule_snapshot or effective_rules(db.get(Tournament,match.tournament_id),db.get(Division,match.division_id),db.get(Stage,match.stage_id),match)
    state=deepcopy(match.state or initial_state(rules))
    if kind=='start':
        if match.status!='ready':raise APIError('Prepare the game and assign a court before starting.',409)
        if not match.entry_a_id or not match.entry_b_id or match.entry_a_id==match.entry_b_id:raise APIError('Resolve two distinct participants before play.',409)
        t=db.get(Tournament,match.tournament_id)
        stage=db.get(Stage,match.stage_id)
        if (match.settings or {}).get('adjudication_required') or (stage.settings or {}).get('qualification_blocked'):raise APIError('Resolve qualification/adjudication issues before this game starts.',409,'qualification_blocked')
        if t.status not in ('published','active'):raise APIError('Publish and validate the tournament before starting.',409)
        from .models import Entry
        for eid in (match.entry_a_id,match.entry_b_id):
            entry=db.get(Entry,eid)
            if entry.status!='active':raise APIError('A participant is inactive. Ask an organizer to correct the entry.',409)
        start_court(match);match.rule_snapshot=deepcopy(rules);state=initial_state(rules);state['clock_anchor']=iso(now());match.status='in_progress';match.actual_start=now()
        if t.status=='published':t.status='active'
    elif kind in ('point','serve','timeout','correct','next_set','undo'):
        if match.status not in ('in_progress','paused'):raise APIError('Start or resume the game before scoring.',409)
        if kind in ('point','timeout') and match.status!='in_progress':raise APIError('Resume play before adding a point or timeout.',409)
        current=state['sets'][state['set_index']]
        if kind=='point':
            side=payload.get('team')
            if side not in ('a','b'):raise APIError('Choose team a or b.')
            if current['complete']:raise APIError('The set is complete. Confirm the next set or End Game.',409,'set_complete')
            current[side]+=1;state['serve']=side;winner=set_winner(current['a'],current['b'],rules,state['set_index'])
            current.update(complete=winner is not None,winner=winner)
        elif kind=='serve':
            if payload.get('team') not in ('a','b'):raise APIError('Choose team a or b.')
            state['serve']=payload['team']
        elif kind=='timeout':
            side=payload.get('team')
            if side not in ('a','b') or current['complete']:raise APIError('Choose a team during an open set.')
            if state['timeouts'][side]<=0:raise APIError('This team has no timeouts remaining.',409)
            state['timeouts'][side]-=1
        elif kind=='correct':
            if not str(payload.get('reason','')).strip():raise APIError('A correction requires a reason.')
            a=check_number(payload.get('a'),'Team A score');b=check_number(payload.get('b'),'Team B score')
            if rules['cap'] and max(a,b)>rules['cap']:raise APIError('A score cannot exceed the configured hard cap.')
            winner=set_winner(a,b,rules,state['set_index']);current.update(a=a,b=b,complete=winner is not None,winner=winner)
        elif kind=='next_set':
            if not current['complete']:raise APIError('Complete this set before starting the next.',409)
            if match_winner(state,rules):raise APIError('The match winning condition is reached. Review and End Game.',409,'match_complete')
            state['sets'].append({'a':0,'b':0,'complete':False,'winner':None});state['set_index']+=1
            state['timeouts']={'a':rules['timeouts'],'b':rules['timeouts']};state['serve']='a' if state['set_index']%2==0 else 'b'
        elif kind=='undo':
            events=list(db.scalars(select(MatchEvent).where(MatchEvent.match_id==match.id).order_by(MatchEvent.sequence.desc())))
            compensated={e.payload.get('compensates') for e in events if e.type=='undo'}
            last=next((e for e in events if e.type in ('point','serve','timeout','correct','next_set') and e.id not in compensated),None)
            if not last:raise APIError('No scoring action is available to undo.',409)
            previous=last.payload.get('before',{})
            for key in ('sets','set_index','serve','timeouts'):state[key]=deepcopy(previous[key])
            payload['compensates']=last.id
    elif kind=='pause':
        if match.status!='in_progress':raise APIError('Only a running game can pause.',409)
        stop_clock(state);match.status='paused'
    elif kind=='resume':
        if match.status!='paused':raise APIError('Only a paused game can resume play.',409)
        state['clock_anchor']=iso(now());match.status='in_progress'
    elif kind=='end':
        if match.status not in ('in_progress','paused'):raise APIError('This game is not open.',409)
        winner=match_winner(state,rules)
        if winner is None:raise APIError('The match is incomplete. Finish play or ask an organizer to record an exceptional outcome.',409,'incomplete_match')
        stop_clock(state);match.status='ended';match.actual_end=now();match.outcome='draw' if winner=='draw' else 'normal'
        match.winner_id=match.entry_a_id if winner=='a' else match.entry_b_id if winner=='b' else None
        release_court(match,ending=True)
        org=db.get(Organization,match.organization_id)
        auto=bool(rules['automatic_approval'] or ((org.settings or {}).get('automatic_approval') or (org.settings or {}).get('auto_finalize'))) and not (match.settings or {}).get('adjudication_required')
        match.result_status='finalized' if auto else 'submitted';match.result_revision+=1
        db.add(ResultRevision(organization_id=match.organization_id,match_id=match.id,revision=match.result_revision,sets=state['sets'],winner_id=match.winner_id,outcome=match.outcome,status=match.result_status,approved_at=now() if auto else None))
    else:raise APIError('Unsupported scoring action.')
    match.state=state
    return rules

@bp.post('/action')
@require_referee
def action():
    data=body();db=get_db()
    if data.get('type')=='end':
        hint=referee_match();db.scalar(select(Tournament).where(Tournament.id==hint.tournament_id).with_for_update())
    match=referee_match(lock=True)
    try:action_id=str(uuid.UUID(data.get('client_action_id','')))
    except (ValueError,TypeError,AttributeError):raise APIError('Supply a unique UUID client action identifier.')
    # Check authority before returning even a duplicate receipt.
    db.refresh(g.scorer)
    if g.scorer.revoked_at or g.scorer.expires_at<now() or g.scorer.generation!=match.generation or data.get('generation')!=match.generation:
        raise APIError('Another device has scoring control. Re-scan and confirm takeover.',403,'scorer_fenced')
    g.scorer.last_seen_at=now()
    duplicate=db.scalar(select(MatchEvent).where(MatchEvent.match_id==match.id,MatchEvent.client_action_id==action_id))
    if duplicate:return response(match,receipt=duplicate.receipt,saved=True,duplicate=True)
    if match.result_status=='finalized':raise APIError('The result is finalized; referee editing is closed.',403)
    if data.get('expected_version')!=match.version:raise APIError('Game state changed. Refresh and review pending actions.',409,'version_conflict',{'current_version':match.version})
    if any(not isinstance(data.get(key),int) or isinstance(data.get(key),bool) for key in ('generation','expected_version')):raise APIError('Generation and expected version must be integers.')
    kind=data.get('type');payload=deepcopy(data.get('payload') or {})
    if not isinstance(payload,dict):raise APIError('Action payload must be an object.')
    allowed={'point':{'team'},'serve':{'team'},'timeout':{'team'},'correct':{'a','b','reason'},'start':set(),'pause':set(),'resume':set(),'next_set':set(),'undo':set(),'end':set()}
    if kind not in allowed or set(payload)-allowed[kind]:raise APIError('Unexpected action type or payload fields.')
    if 'reason' in payload and (not isinstance(payload['reason'],str) or len(payload['reason'])>2000):raise APIError('Correction reason must be text up to 2000 characters.')
    before=deepcopy(match.state or {});payload['before']=before
    old_version=match.version;mutate(match,kind,payload)
    match.version+=1
    payload['after']=deepcopy(match.state)
    payload['lifecycle']={'status':match.status,'actual_start':iso(match.actual_start),'actual_end':iso(match.actual_end),'winner_id':match.winner_id,'outcome':match.outcome}
    if kind=='start':payload['rule_snapshot']=deepcopy(match.rule_snapshot)
    receipt={'client_action_id':action_id,'version':match.version,'saved_at':iso(now())}
    event=MatchEvent(organization_id=match.organization_id,match_id=match.id,sequence=match.version,client_action_id=action_id,type=kind,session_id=g.scorer.id,payload=payload,expected_version=old_version,new_version=match.version,receipt=receipt)
    db.add(event);audit('score.'+str(kind),'match',match.id,before=before,after=match.state,reason=payload.get('reason'))
    emit(match,'score.saved')
    if kind=='end' and match.result_status=='finalized':
        from .competition import advance_results
        advance_results(match)
    db.commit();return response(match,receipt=receipt,saved=True)
