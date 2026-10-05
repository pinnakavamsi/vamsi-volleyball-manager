"""Reproducible isolated local physical-backup/WAL recovery drill.

Changes ONLY instance/postgres development cluster; restores to a separate
empty instance/restore-validation directory and turns source archiving off
again after the drill. No production configuration or database is contacted.
"""
import base64,json,os,secrets,shlex,subprocess,sys,time,uuid
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
from volleyball import create_app
ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
CLUSTER=ROOT/'instance'/'postgres';STORE=ROOT/'instance'/'recovery-material';DEST=ROOT/'instance'/os.environ.get('RESTORE_DRILL_DIRECTORY','restore-validation-3');KEY=ROOT/'instance'/'recovery-key'
if DEST.parent!=ROOT/'instance' or DEST.name in {'postgres','files','recovery-material'}:raise SystemExit('Use a new direct child of instance for the isolated restore drill.')
if not (CLUSTER/'PG_VERSION').exists():raise SystemExit('Run isolated local_database.py first.')
if DEST.exists() and any(DEST.iterdir()):raise SystemExit('Existing restore validation preserved; choose/review a new destination for another drill.')
owner=make_url(os.environ['MIGRATION_DATABASE_URL'])
if owner.host!='127.0.0.1' or owner.port!=55432 or owner.database!='volleyball':raise SystemExit('This drill is restricted to the generated local development database.')
bin_dir=Path(os.environ.get('POSTGRES_BIN','/opt/homebrew/opt/postgresql@18/bin'))
os.environ['PATH']=str(bin_dir)+os.pathsep+os.environ['PATH']
STORE.mkdir(parents=True,exist_ok=True,mode=0o700)
if not KEY.exists():KEY.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)));KEY.chmod(0o600)
files=ROOT/'instance'/'files';files.mkdir(exist_ok=True);(files/'restore-probe.txt').write_text('Volleyball Manager isolated restore file integrity probe.\n')
started=time.perf_counter();dsn=owner.set(drivername='postgresql').render_as_string(hide_password=False)
command=' '.join(shlex.quote(v) for v in [str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/backup_wal.py'),'%p','%f','--storage',str(STORE),'--key-file',str(KEY)])
archiving=False;restored=False
try:
    with psycopg.connect(dsn,autocommit=True) as conn:
        conn.execute(sql.SQL('ALTER SYSTEM SET archive_command = {}').format(sql.Literal(command)))
        conn.execute("ALTER SYSTEM SET archive_mode = 'on'")
        conn.execute("ALTER SYSTEM SET archive_timeout = '60s'")
        conn.execute("ALTER SYSTEM SET wal_level = 'replica'")
    subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(CLUSTER),'-m','fast','restart','-l',str(ROOT/'instance'/'postgres.log')],check=True,stdout=subprocess.DEVNULL)
    archiving=True
    env=dict(os.environ);env['BASEBACKUP_DATABASE_URL']=os.environ['BACKUP_DATABASE_URL']
    base=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/backup_base.py'),'--storage',str(STORE),'--files',str(files),'--key-file',str(KEY)],env=env,check=True,capture_output=True,text=True)
    package=STORE/json.loads(base.stdout)['file']
    # Accept a real delegated score AFTER the physical base backup completed.
    access=(ROOT/'instance'/'demo-access.txt').read_text().splitlines()
    email=next(line.split(': ',1)[1] for line in access if line.startswith('Organizer email: '))
    password=next(line.split(': ',1)[1] for line in access if line.startswith('Organizer password: '))
    demo=json.loads((ROOT/'instance'/'demo.json').read_text());oid=demo['organizations'][0]['id'];mid=demo['live_match_id']
    app=create_app();client=app.test_client();csrf=client.get('/api/v1/auth/me').json['csrf_token']
    login=client.post('/api/v1/auth/login',json={'email':email,'password':password},headers={'X-CSRF-Token':csrf});assert login.status_code==200,login.json
    csrf=client.get('/api/v1/auth/me').json['csrf_token'];headers={'X-CSRF-Token':csrf,'X-Organization-ID':oid}
    grant=client.post('/api/v1/matches/'+mid+'/qr',json={},headers=headers);assert grant.status_code==200,grant.json
    ref=app.test_client();exchange=ref.post('/api/v1/referee/exchange',json={'token':grant.json['url'].split('#')[1]});assert exchange.status_code==200,exchange.json
    rheaders={'X-CSRF-Token':exchange.json['csrf_token']};take=ref.post('/api/v1/referee/resume',json={'takeover':True},headers=rheaders);assert take.status_code==200,take.json
    match=take.json['match'];action_id=str(uuid.uuid4())
    score=ref.post('/api/v1/referee/action',json={'client_action_id':action_id,'generation':take.json['session']['generation'],'expected_version':match['version'],'type':'point','payload':{'team':'a'}},headers=rheaders)
    assert score.status_code==200 and score.json['saved'],score.json
    saved=score.json['match'];receipt=score.json['receipt']
    with psycopg.connect(dsn,autocommit=True) as conn:
        before_orgs=conn.execute('SELECT id FROM organizations ORDER BY id').fetchall()
        before_memberships=conn.execute('SELECT organization_id,user_id,role,status FROM memberships ORDER BY id').fetchall()
        conn.execute('SELECT pg_switch_wal()')
        end_lsn=conn.execute('SELECT pg_current_wal_lsn()').fetchone()[0]
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        with psycopg.connect(dsn,autocommit=True) as conn:
            pending=conn.execute("SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready'").fetchone()[0]
        if pending==0 and (STORE/'wal-status.json').exists():break
        time.sleep(.25)
    else:raise RuntimeError('WAL archive did not acknowledge before deadline.')
    env['RESTORE_ISOLATED']='yes';restore_started=time.perf_counter()
    result=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/restore_pitr.py'),str(package),'--storage',str(STORE),'--key-file',str(KEY),'--destination',str(DEST),'--port','55433','--start'],env=env,check=True,capture_output=True,text=True)
    restored=True
    restored_url=owner.set(drivername='postgresql',port=55433).render_as_string(hide_password=False)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        with psycopg.connect(restored_url,autocommit=True) as conn:
            if not conn.execute('SELECT pg_is_in_recovery()').fetchone()[0]:break
        time.sleep(.25)
    else:raise RuntimeError('Isolated recovery did not finish WAL replay within the validation deadline.')
    with psycopg.connect(restored_url,autocommit=True) as conn:
        assert conn.execute('SELECT pg_is_in_recovery()').fetchone()[0] is False
        assert conn.execute('SELECT id FROM organizations ORDER BY id').fetchall()==before_orgs
        assert conn.execute('SELECT organization_id,user_id,role,status FROM memberships ORDER BY id').fetchall()==before_memberships
        row=conn.execute('SELECT version,state,rule_snapshot FROM matches WHERE id=%s',[mid]).fetchone()
        assert row[0]==saved['version'] and row[1]==saved['state'] and row[2]==saved['rules']
        event=conn.execute('SELECT receipt FROM match_events WHERE match_id=%s AND client_action_id=%s',[mid,action_id]).fetchone()[0]
        assert event==receipt
        occupancy=conn.execute('SELECT status FROM court_assignments WHERE match_id=%s AND released_at IS NULL',[mid]).fetchone()[0];assert occupancy=='occupied'
        assert conn.execute('SELECT count(*) FROM audit_events').fetchone()[0]>0
        assert conn.execute('SELECT count(*) FROM result_revisions').fetchone()[0]>0
    assert (DEST/'files'/'restore-probe.txt').read_bytes()==(files/'restore-probe.txt').read_bytes()
    # Verify runtime RLS against recovered data and membership isolation.
    runtime=make_url(os.environ['DATABASE_URL']).set(drivername='postgresql',port=55433).render_as_string(hide_password=False)
    with psycopg.connect(runtime) as conn:
        assert conn.execute('SELECT count(*) FROM matches').fetchone()[0]==0
        conn.execute("SELECT set_config('app.organization_id',%s,true)",[oid]);assert conn.execute('SELECT count(*) FROM matches').fetchone()[0]>0
    report={'validated_at':datetime.now(timezone.utc).isoformat(),'environment':'isolated local macOS PostgreSQL18','status':'passed',
        'encrypted_physical_base':True,'separate_backup_role':True,'continuous_wal_replayed':True,'score_committed_after_base_survived':True,'score_version':saved['version'],
        'organizations_preserved':len(before_orgs),'memberships_preserved':len(before_memberships),'court_occupancy_preserved':True,'file_integrity_verified':True,'runtime_rls_verified':True,
        'isolated_restore_seconds':round(time.perf_counter()-restore_started,3),'total_drill_seconds':round(time.perf_counter()-started,3),
        'production_rpo_rto_measured':False,'source_archiving_after_drill':'disabled; enable independent recovery storage before production'}
    (ROOT/'docs'/'restore-validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:
    if restored:
        subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(DEST/'data'),'-m','fast','stop'],stdout=subprocess.DEVNULL,check=False)
    if archiving:
        with psycopg.connect(dsn,autocommit=True) as conn:
            conn.execute("ALTER SYSTEM SET archive_mode = 'off'");conn.execute("ALTER SYSTEM RESET archive_command");conn.execute("ALTER SYSTEM RESET archive_timeout")
        subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(CLUSTER),'-m','fast','restart','-l',str(ROOT/'instance'/'postgres.log')],stdout=subprocess.DEVNULL,check=True)
