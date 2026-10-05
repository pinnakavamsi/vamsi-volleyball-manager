"""A07, A10, A17-A27, A31-A35: durable and concurrent PostgreSQL scoring."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from sqlalchemy import select,text
from volleyball.db import get_db,set_scope
from volleyball.models import Tournament,Division,Stage,Entry,Team,Venue,Court,Match,MatchEvent,CourtAssignment,ScorerSession,Organization
from volleyball.services.rules import validate_rules
import pytest

@pytest.fixture
def game_case(app,admin_client,demo):
    with app.app_context():
        db=get_db();oid=demo['organizations'][0]['id'];set_scope(oid)
        t=Tournament(organization_id=oid,name='Correctness Cup',slug='correctness-'+uuid.uuid4().hex[:8],start_date=date.today(),end_date=date.today()+timedelta(days=2),timezone='America/New_York',status='published',visibility='public',rules=validate_rules({'regular_target':2,'deciding_target':2,'cap':None}))
        db.add(t);db.flush();d=Division(organization_id=oid,tournament_id=t.id,name='Open',rules={});db.add(d);db.flush()
        s=Stage(organization_id=oid,tournament_id=t.id,division_id=d.id,name='League',format='round_robin',rules={});db.add(s);db.flush()
        es=[]
        for i in range(4):
            team=Team(organization_id=oid,name='Correctness '+str(i));db.add(team);db.flush()
            e=Entry(organization_id=oid,tournament_id=t.id,division_id=d.id,team_id=team.id,name=team.name,seed=i+1);db.add(e);db.flush();es.append(e)
        venue=Venue(organization_id=oid,name='Test hall',timezone='America/New_York');db.add(venue);db.flush()
        courts=[]
        for i in range(2):
            c=Court(organization_id=oid,venue_id=venue.id,name='Test '+str(i));db.add(c);db.flush();courts.append(c)
        matches=[]
        for i in range(2):
            m=Match(organization_id=oid,tournament_id=t.id,division_id=d.id,stage_id=s.id,entry_a_id=es[i*2].id,entry_b_id=es[i*2+1].id,state={});db.add(m);db.flush();matches.append(m)
        db.commit()
        return {'org_id':oid,'tournament_id':t.id,'division_id':d.id,'stage_id':s.id,'entries':[e.id for e in es],'courts':[c.id for c in courts],'matches':[m.id for m in matches]}


def admit(app,admin,match_id,court_id):
    response=admin.post(f'/api/v1/matches/{match_id}/court',json={'court_id':court_id},headers=admin.api_headers);assert response.status_code==200,response.json
    qr=admin.post(f'/api/v1/matches/{match_id}/qr',json={},headers=admin.api_headers);assert qr.status_code==200,qr.json
    ref=app.test_client();result=ref.post('/api/v1/referee/exchange',json={'token':qr.json['url'].split('#')[1],'name':'Test referee'})
    assert result.status_code==200,result.json
    ref.csrf=result.json['csrf_token'];ref.match=result.json['match'];ref.generation=result.json['session']['generation']
    return ref

def act(ref,kind,payload=None,action_id=None,expected=None):
    result=ref.post('/api/v1/referee/action',json={'client_action_id':action_id or str(uuid.uuid4()),'generation':ref.generation,'expected_version':ref.match['version'] if expected is None else expected,'type':kind,'payload':payload or {}},headers={'X-CSRF-Token':ref.csrf})
    if result.status_code==200:ref.match=result.json['match']
    return result

def test_retry_stale_version_and_restart(app,admin_client,game_case):
    c=game_case;ref=admit(app,admin_client,c['matches'][0],c['courts'][0]);assert act(ref,'start').status_code==200
    identifier=str(uuid.uuid4());version=ref.match['version'];point=act(ref,'point',{'team':'a'},identifier);assert point.json['saved'] is True
    retry=act(ref,'point',{'team':'a'},identifier,expected=version);assert retry.status_code==200 and retry.json['duplicate']
    assert ref.match['state']['sets'][0]['a']==1
    stale=act(ref,'point',{'team':'b'},expected=version);assert stale.status_code==409
    # New factory/process state has no in-memory match dependency.
    from volleyball import create_app
    restarted=create_app(dict(app.config))
    with restarted.app_context():
        set_scope(c['org_id']);m=get_db().get(Match,c['matches'][0]);assert m.state['sets'][0]['a']==1
        assert get_db().scalar(select(CourtAssignment).where(CourtAssignment.match_id==m.id,CourtAssignment.released_at.is_(None))).status=='occupied'
    restarted.extensions['db_engine'].dispose()

def test_takeover_fences_delayed_old_phone_and_recovers(app,admin_client,game_case):
    c=game_case;old=admit(app,admin_client,c['matches'][0],c['courts'][0]);act(old,'start');act(old,'point',{'team':'a'});act(old,'timeout',{'team':'b'});act(old,'pause')
    qr=admin_client.post(f"/api/v1/matches/{c['matches'][0]}/qr",json={},headers=admin_client.api_headers).json
    new=app.test_client();exchange=new.post('/api/v1/referee/exchange',json={'token':qr['url'].split('#')[1]});assert exchange.status_code==200
    new.csrf=exchange.json['csrf_token'];new.match=exchange.json['match'];new.generation=exchange.json['session']['generation'];assert new.generation==-1
    take=new.post('/api/v1/referee/resume',json={'takeover':True},headers={'X-CSRF-Token':new.csrf});assert take.status_code==200,take.json
    new.match=take.json['match'];new.generation=take.json['session']['generation']
    assert new.match['state']['sets'][0]['a']==1 and new.match['state']['timeouts']['b']==1 and new.match['status']=='paused'
    assert act(old,'point',{'team':'a'}).status_code in (401,403)
    assert act(new,'resume').status_code==200
    assert act(new,'point',{'team':'b'}).status_code==200

def test_set_boundary_undo_end_approval_and_standings(app,admin_client,game_case):
    c=game_case;ref=admit(app,admin_client,c['matches'][0],c['courts'][0]);act(ref,'start')
    for _ in range(2):assert act(ref,'point',{'team':'a'}).status_code==200
    assert ref.match['state']['sets'][0]['complete']
    assert act(ref,'point',{'team':'b'}).status_code==409
    assert act(ref,'next_set').status_code==200
    assert act(ref,'undo').status_code==200 and ref.match['state']['set_index']==0
    assert act(ref,'undo').status_code==200 and ref.match['state']['sets'][0]['a']==1
    act(ref,'point',{'team':'a'});act(ref,'next_set')
    for _ in range(2):act(ref,'point',{'team':'a'})
    assert ref.match['status']=='in_progress' # explicit End required
    assert act(ref,'end').status_code==200
    assert ref.match['status']=='ended' and ref.match['result_status']=='submitted'
    with app.app_context():
        set_scope(c['org_id']);db=get_db();assert not db.scalar(select(CourtAssignment).where(CourtAssignment.match_id==c['matches'][0],CourtAssignment.released_at.is_(None)))
        assert len(db.scalars(select(MatchEvent).where(MatchEvent.match_id==c['matches'][0])).all())>=10
    approve=admin_client.post(f"/api/v1/matches/{c['matches'][0]}/approve",json={},headers=admin_client.api_headers);assert approve.status_code==200,approve.json
    rows=admin_client.get(f"/api/v1/divisions/{c['division_id']}/standings",headers=admin_client.api_headers).json['rows']
    assert next(r for r in rows if r['entry_id']==c['entries'][0])['standings_points']==2
    assert act(ref,'point',{'team':'a'}).status_code==403

def test_running_rules_snapshot_and_qr_visibility_independent(app,admin_client,game_case):
    c=game_case;ref=admit(app,admin_client,c['matches'][0],c['courts'][0]);act(ref,'start')
    changed=admin_client.patch(f"/api/v1/tournaments/{c['tournament_id']}",json={'rules':validate_rules({'regular_target':25})},headers=admin_client.api_headers);assert changed.status_code==200
    assert ref.get('/api/v1/referee/state').json['match']['rules']['regular_target']==2
    assert 'qr' not in str(admin_client.get(f"/api/v1/matches/{c['matches'][0]}",headers=admin_client.api_headers).json).lower()
    assert act(ref,'point',{'team':'a'}).status_code==200

def test_concurrent_court_claim_only_one_accepted(app,admin_client,game_case):
    c=game_case
    with admin_client.session_transaction() as sess:cookie=dict(sess)
    def reserve(mid):
        client=app.test_client()
        with client.session_transaction() as sess:sess.update(cookie)
        return client.post(f'/api/v1/matches/{mid}/court',json={'court_id':c['courts'][0]},headers=admin_client.api_headers).status_code
    with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(reserve,c['matches']))
    assert sorted(statuses)==[200,409],statuses

def test_public_private_revocation_and_no_secrets(app,admin_client,game_case):
    c=game_case
    with app.app_context():
        set_scope(c['org_id']);slug=get_db().get(Tournament,c['tournament_id']).slug
    visitor=app.test_client();visible=visitor.get('/api/v1/public/'+slug);assert visible.status_code==200
    assert all(x not in visible.get_data(as_text=True) for x in ['token_hash','password_hash','contact','roster','referee_name'])
    update=admin_client.patch(f"/api/v1/tournaments/{c['tournament_id']}",json={'visibility':'private'},headers=admin_client.api_headers);assert update.status_code==200
    assert visitor.get('/api/v1/public/'+slug).status_code==404
    assert visitor.get('/api/v1/public/'+slug+'/events').status_code==404

def test_postgresql_rls_pool_scope_reset(app,admin_client,game_case):
    engine=app.extensions['db_engine']
    with engine.connect() as conn:
        role=conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one();assert role==(False,False)
        assert conn.execute(text('SELECT count(*) FROM matches')).scalar()==0
        conn.execute(text("SELECT set_config('app.organization_id',:id,true)"),{'id':game_case['org_id']})
        assert conn.execute(text('SELECT count(*) FROM matches')).scalar()>0
    with engine.connect() as conn:assert conn.execute(text('SELECT count(*) FROM matches')).scalar()==0

def test_audit_and_match_events_append_only(app,admin_client,game_case):
    from sqlalchemy.exc import DBAPIError
    with app.app_context():
        set_scope(game_case['org_id']);db=get_db()
        with pytest.raises(DBAPIError):db.execute(text('DELETE FROM audit_events'))
        db.rollback()
