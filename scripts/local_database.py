"""Create/start only this project's isolated development PostgreSQL cluster."""
import os, pathlib, secrets, shutil, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTANCE = ROOT/'instance'
CLUSTER = INSTANCE/'postgres'
BIN = pathlib.Path(os.environ.get('POSTGRES_BIN','/opt/homebrew/opt/postgresql@18/bin'))
PORT = os.environ.get('POSTGRES_PORT','55432')
INSTANCE.mkdir(exist_ok=True)
if not CLUSTER.exists():
    owner_password = secrets.token_urlsafe(32)
    app_password = secrets.token_urlsafe(32)
    env = ROOT/'.env'
    if env.exists(): raise SystemExit('Refusing to initialize a cluster alongside an existing .env; review its database connection first.')
    pwfile = INSTANCE/'pg-init-password'
    pwfile.write_text(owner_password)
    pwfile.chmod(0o600)
    subprocess.run([str(BIN/'initdb'),'-D',str(CLUSTER),'-U','volleyball_owner','--auth-local=trust','--auth-host=scram-sha-256','--pwfile',str(pwfile)], check=True)
    pwfile.unlink()
    env.write_text(f'DATABASE_URL=postgresql+psycopg://volleyball_app:{app_password}@127.0.0.1:{PORT}/volleyball\nMIGRATION_DATABASE_URL=postgresql+psycopg://volleyball_owner:{owner_password}@127.0.0.1:{PORT}/volleyball\nTEST_DATABASE_URL=postgresql+psycopg://volleyball_owner:{owner_password}@127.0.0.1:{PORT}/volleyball_test\nTEST_RUNTIME_URL=postgresql+psycopg://volleyball_app:{app_password}@127.0.0.1:{PORT}/volleyball_test\nSECRET_KEY={secrets.token_hex(48)}\nAPP_ENV=development\nSESSION_COOKIE_SECURE=false\nPUBLIC_BASE_URL=http://127.0.0.1:5050\nWEATHER_PROVIDER=disabled\n')
    env.chmod(0o600)
status = subprocess.run([str(BIN/'pg_ctl'),'-D',str(CLUSTER),'status'],stdout=subprocess.DEVNULL)
if status.returncode:
    subprocess.run([str(BIN/'pg_ctl'),'-D',str(CLUSTER),'-l',str(INSTANCE/'postgres.log'),'-o',f"-p {PORT} -h 127.0.0.1 -c unix_socket_directories=''",'start'],check=True)
# Environment loading stays local and no credentials are printed.
from dotenv import dotenv_values, set_key
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
cfg=dotenv_values(ROOT/'.env')
owner_url=make_url(cfg['MIGRATION_DATABASE_URL'])
owner_uri=owner_url.set(drivername='postgresql',database='postgres').render_as_string(hide_password=False)
app_password=make_url(cfg['DATABASE_URL']).password
if not cfg.get('BACKUP_DATABASE_URL'):
    backup_url=owner_url.set(username='volleyball_backup',password=secrets.token_urlsafe(32))
    set_key(ROOT/'.env','BACKUP_DATABASE_URL',backup_url.render_as_string(hide_password=False),quote_mode='never')
    (ROOT/'.env').chmod(0o600)
    cfg=dotenv_values(ROOT/'.env')
backup_password=make_url(cfg['BACKUP_DATABASE_URL']).password
with psycopg.connect(owner_uri,autocommit=True) as con:
    if not con.execute("SELECT 1 FROM pg_roles WHERE rolname='volleyball_app'").fetchone():
        con.execute(sql.SQL('CREATE ROLE volleyball_app LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS').format(sql.Literal(app_password)))
    if not con.execute("SELECT 1 FROM pg_roles WHERE rolname='volleyball_backup'").fetchone():
        con.execute(sql.SQL('CREATE ROLE volleyball_backup LOGIN PASSWORD {} NOSUPERUSER REPLICATION BYPASSRLS').format(sql.Literal(backup_password)))
    con.execute('GRANT pg_read_all_data,pg_read_all_settings TO volleyball_backup')
    for name in ['volleyball','volleyball_test']:
        if not con.execute('SELECT 1 FROM pg_database WHERE datname=%s',[name]).fetchone():
            con.execute(sql.SQL('CREATE DATABASE {} OWNER volleyball_owner').format(sql.Identifier(name)))
print('Project PostgreSQL is running on 127.0.0.1:'+PORT+'; local credentials saved in .env.')
