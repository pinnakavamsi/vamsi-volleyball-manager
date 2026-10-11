"""Reproducible isolated local physical-backup/WAL recovery drill.

Requires a separate disposable instance/restore-source-* cluster and explicit
fixture identifiers. Never uses the ordinary local application cluster or
saved credentials. Source archiving is disabled again after the drill.
"""
import argparse,base64,getpass,json,os,re,secrets,shlex,socket,subprocess,sys,time,uuid
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
from volleyball import create_app
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-cluster',required=True,help='Separate disposable instance/restore-source-* PostgreSQL directory.')
parser.add_argument('--email',required=True,help='Synthetic fixture email ending in .test or .invalid.')
parser.add_argument('--organization-id',required=True)
parser.add_argument('--match-id',required=True,help='Existing synthetic in-progress fixture match.')
parser.add_argument('--restore-port',type=int,default=55434)
parser.add_argument('--confirm-disposable-source',action='store_true')
args=parser.parse_args()
if not args.confirm_disposable_source:parser.error('Explicit --confirm-disposable-source is required; source archiving and one synthetic score will change.')
if not args.email.rsplit('@',1)[-1].endswith(('.test','.invalid')):parser.error('Use a synthetic fixture email.')
try:uuid.UUID(args.organization_id);uuid.UUID(args.match_id)
except ValueError:parser.error('Organization and match IDs must be UUIDs.')
if not 1024<=args.restore_port<=65535:parser.error('Use an unprivileged isolated restore port.')
password=getpass.getpass('Synthetic fixture password: ')
email,oid,mid=args.email,args.organization_id,args.match_id
load_dotenv(ROOT/'.env')
CLUSTER=Path(args.source_cluster).resolve();STORE=ROOT/'instance'/'recovery-material';DEST=ROOT/'instance'/os.environ.get('RESTORE_DRILL_DIRECTORY','restore-validation-'+secrets.token_hex(6));KEY=ROOT/'instance'/'recovery-key'
if CLUSTER.parent!=(ROOT/'instance').resolve() or not CLUSTER.name.startswith('restore-source-'):raise SystemExit('Source must be a separate disposable instance/restore-source-* cluster; the application cluster is forbidden.')
if DEST.parent!=ROOT/'instance' or DEST.name in {'postgres','files','recovery-material'}:raise SystemExit('Use a new direct child of instance for the isolated restore drill.')
if not (CLUSTER/'PG_VERSION').exists() or not (CLUSTER/'postmaster.pid').exists():raise SystemExit('Start the separately provisioned disposable source cluster first.')
if DEST.exists() and any(DEST.iterdir()):raise SystemExit('Existing restore validation preserved; choose/review a new destination for another drill.')
owner=make_url(os.environ['MIGRATION_DATABASE_URL'])
runtime_url=make_url(os.environ['DATABASE_URL'])
backup_url=make_url(os.environ['BACKUP_DATABASE_URL'])
metadata=(CLUSTER/'postmaster.pid').read_text().splitlines()
if owner.host!='127.0.0.1' or not re.search(r'(?:^|_)(?:demo|test)(?:_|$)',owner.database or '') or Path(metadata[1]).resolve()!=CLUSTER or int(metadata[3])!=owner.port or (runtime_url.host,runtime_url.port,runtime_url.database)!=(owner.host,owner.port,owner.database):raise SystemExit('Set matching owner/runtime URLs for the named disposable demo/test source cluster.')
if args.restore_port==owner.port:raise SystemExit('Source and restore ports must differ.')
if (backup_url.host,backup_url.port,backup_url.database)!=(owner.host,owner.port,owner.database):raise SystemExit('Backup credentials must target the same disposable source cluster.')
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',args.restore_port))==0:raise SystemExit('Restore port is in use; no source archiving was changed.')
from scripts.local_database import postgres_bin
bin_dir=postgres_bin()
os.environ['PATH']=str(bin_dir)+os.pathsep+os.environ['PATH']
STORE.mkdir(parents=True,exist_ok=True,mode=0o700)
if not KEY.exists():KEY.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)));KEY.chmod(0o600)
files=ROOT/'instance'/'files';files.mkdir(exist_ok=True);(files/'restore-probe.txt').write_text('Volleyball Manager isolated restore file integrity probe.\n')
started=time.perf_counter();dsn=owner.set(drivername='postgresql').render_as_string(hide_password=False)
with psycopg.connect(dsn) as connection:
    demo_match=connection.execute("SELECT o.demo, m.settings->>'demo' FROM matches m JOIN organizations o ON o.id=m.organization_id WHERE m.id=%s AND o.id=%s",[mid,oid]).fetchone()
    if not demo_match or demo_match!=(True,'true'):raise SystemExit('Choose an explicitly synthetic fixture match in a demo organization; no archiving was changed.')
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
    result=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/restore_pitr.py'),str(package),'--storage',str(STORE),'--key-file',str(KEY),'--destination',str(DEST),'--port',str(args.restore_port),'--start'],env=env,check=True,capture_output=True,text=True)
    restored=True
    restored_url=owner.set(drivername='postgresql',port=args.restore_port).render_as_string(hide_password=False)
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
    runtime=runtime_url.set(drivername='postgresql',port=args.restore_port).render_as_string(hide_password=False)
    with psycopg.connect(runtime) as conn:
        assert conn.execute('SELECT count(*) FROM matches').fetchone()[0]==0
        conn.execute("SELECT set_config('app.organization_id',%s,true)",[oid]);assert conn.execute('SELECT count(*) FROM matches').fetchone()[0]>0
    report={'validated_at':datetime.now(timezone.utc).isoformat(),'environment':'isolated local macOS PostgreSQL18','status':'passed',
        'encrypted_physical_base':True,'separate_backup_role':True,'continuous_wal_replayed':True,'score_committed_after_base_survived':True,'score_version':saved['version'],
        'organizations_preserved':len(before_orgs),'memberships_preserved':len(before_memberships),'court_occupancy_preserved':True,'file_integrity_verified':True,'runtime_rls_verified':True,
        'isolated_restore_seconds':round(time.perf_counter()-restore_started,3),'total_drill_seconds':round(time.perf_counter()-started,3),
        'production_rpo_rto_measured':False,'source_archiving_after_drill':'disabled; enable independent recovery storage before production'}
    (DEST/'validation-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:
    if restored:
        subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(DEST/'data'),'-m','fast','stop'],stdout=subprocess.DEVNULL,check=False)
    if archiving:
        with psycopg.connect(dsn,autocommit=True) as conn:
            conn.execute("ALTER SYSTEM SET archive_mode = 'off'");conn.execute("ALTER SYSTEM RESET archive_command");conn.execute("ALTER SYSTEM RESET archive_timeout")
        subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(CLUSTER),'-m','fast','restart','-l',str(ROOT/'instance'/'postgres.log')],stdout=subprocess.DEVNULL,check=True)
