"""Create/start only this project's isolated loopback development PostgreSQL cluster."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess

ROOT=Path(__file__).resolve().parents[1]
INSTANCE=ROOT/'instance'
CLUSTER=INSTANCE/'postgres'
DEFAULT_PORT=55433

def postgres_bin():
    if os.environ.get('POSTGRES_BIN'):return Path(os.environ['POSTGRES_BIN'])
    installed=shutil.which('initdb')
    if installed:return Path(installed).resolve().parent
    for candidate in [Path('/opt/homebrew/opt/postgresql@18/bin'),Path('/usr/lib/postgresql/18/bin')]:
        if (candidate/'initdb').is_file():return candidate
    raise SystemExit('PostgreSQL 18 tools are required. Install them or set POSTGRES_BIN to their directory.')

def write_private(path,text):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(text)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=int(os.environ.get('POSTGRES_PORT',DEFAULT_PORT)))
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Use an unprivileged PostgreSQL port between 1024 and 65535.')
    port=args.port;binary=postgres_bin();env=ROOT/'.env'
    if INSTANCE.is_symlink() or CLUSTER.is_symlink() or env.is_symlink():raise SystemExit('Project instance, PostgreSQL cluster and .env must not be symlinks.')
    INSTANCE.mkdir(exist_ok=True,mode=0o700);INSTANCE.chmod(0o700)
    from dotenv import dotenv_values,set_key
    from sqlalchemy.engine import make_url
    if CLUSTER.exists():
        if not env.is_file() or not (CLUSTER/'PG_VERSION').is_file():raise SystemExit('Existing cluster/configuration is incomplete. Preserve it and review before continuing.')
        cfg=dotenv_values(env)
        for key,role in [('DATABASE_URL','volleyball_app'),('MIGRATION_DATABASE_URL','volleyball_owner')]:
            try:url=make_url(cfg[key])
            except Exception:raise SystemExit('Existing database configuration is invalid. It was preserved.') from None
            if url.host!='127.0.0.1' or url.port!=port or url.database!='volleyball' or url.username!=role:
                raise SystemExit('Existing .env does not target this project cluster on the selected port. Review it or perform an explicitly authorized local reset; no connections were changed.')
    else:
        if env.exists():raise SystemExit('Refusing to initialize a cluster alongside an existing .env. Review/reset this project configuration explicitly first.')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:raise SystemExit('Selected port is already in use. Choose a free project-specific port; no cluster was initialized.')
        owner_password,app_password,backup_password=[secrets.token_urlsafe(32) for _ in range(3)]
        pwfile=INSTANCE/'pg-init-password'
        write_private(pwfile,owner_password)
        try:
            subprocess.run([str(binary/'initdb'),'-D',str(CLUSTER),'-U','volleyball_owner','--auth-local=scram-sha-256','--auth-host=scram-sha-256','--pwfile',str(pwfile)],check=True)
        finally:pwfile.unlink(missing_ok=True)
        values={
            'DATABASE_URL':f'postgresql+psycopg://volleyball_app:{app_password}@127.0.0.1:{port}/volleyball',
            'MIGRATION_DATABASE_URL':f'postgresql+psycopg://volleyball_owner:{owner_password}@127.0.0.1:{port}/volleyball',
            'BACKUP_DATABASE_URL':f'postgresql+psycopg://volleyball_backup:{backup_password}@127.0.0.1:{port}/volleyball',
            'TEST_DATABASE_URL':f'postgresql+psycopg://volleyball_owner:{owner_password}@127.0.0.1:{port}/volleyball_test',
            'TEST_RUNTIME_URL':f'postgresql+psycopg://volleyball_app:{app_password}@127.0.0.1:{port}/volleyball_test',
            'SECRET_KEY':secrets.token_hex(48),'MFA_ENCRYPTION_KEY':base64.urlsafe_b64encode(os.urandom(32)).decode(),
            'APP_ENV':'development','SESSION_COOKIE_SECURE':'false','PUBLIC_BASE_URL':'http://127.0.0.1:5050',
            'EMAIL_DELIVERY_MODE':'deferred','GEOCODING_PROVIDER':'','GEOCODING_LICENSE_CONFIRMED':'false','WEATHER_LICENSE_CONFIRMED':'false',
        }
        write_private(env,'# Private local infrastructure configuration; no application accounts.\n'+''.join(f'{key}={value}\n' for key,value in values.items()))
    env.chmod(0o600)
    status=subprocess.run([str(binary/'pg_ctl'),'-D',str(CLUSTER),'status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if status.returncode:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:raise SystemExit('Selected port belongs to another running process. Existing cluster and secrets were preserved.')
        subprocess.run([str(binary/'pg_ctl'),'-D',str(CLUSTER),'-l',str(INSTANCE/'postgres.log'),'-o',f"-p {port} -h 127.0.0.1 -c unix_socket_directories='' -c log_statement=none -c log_min_error_statement=panic",'start'],check=True)
    else:
        pidfile=(CLUSTER/'postmaster.pid').read_text().splitlines()
        if Path(pidfile[1]).resolve()!=CLUSTER.resolve() or int(pidfile[3])!=port:raise SystemExit('The existing cluster runs on another port; its state was preserved.')
    cfg=dotenv_values(env);owner_url=make_url(cfg['MIGRATION_DATABASE_URL'])
    if not cfg.get('BACKUP_DATABASE_URL'):
        backup_url=owner_url.set(username='volleyball_backup',password=secrets.token_urlsafe(32))
        set_key(env,'BACKUP_DATABASE_URL',backup_url.render_as_string(hide_password=False),quote_mode='never');env.chmod(0o600);cfg=dotenv_values(env)
    import psycopg
    from psycopg import sql
    owner_uri=owner_url.set(drivername='postgresql',database='postgres').render_as_string(hide_password=False)
    with psycopg.connect(owner_uri,autocommit=True) as connection:
        for role,password,privileges in [('volleyball_app',make_url(cfg['DATABASE_URL']).password,'NOSUPERUSER NOBYPASSRLS'),('volleyball_backup',make_url(cfg['BACKUP_DATABASE_URL']).password,'NOSUPERUSER REPLICATION BYPASSRLS')]:
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',[role]).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} ').format(sql.Identifier(role),sql.Literal(password))+sql.SQL(privileges))
        connection.execute('GRANT pg_read_all_data,pg_read_all_settings TO volleyball_backup')
        for name in ['volleyball','volleyball_test']:
            if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',[name]).fetchone():connection.execute(sql.SQL('CREATE DATABASE {} OWNER volleyball_owner').format(sql.Identifier(name)))
    print(f'Project PostgreSQL is running on 127.0.0.1:{port}. Private infrastructure secrets are in .env; no application accounts or demo data were created.')

if __name__=='__main__':main()
