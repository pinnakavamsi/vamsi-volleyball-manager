#!/usr/bin/env python3
"""Encrypted physical PostgreSQL base backup plus durable application files.

Requires an operator replication connection in BASEBACKUP_DATABASE_URL and
configured continuous WAL archiving. No production settings are changed here.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from backup_crypto import encrypt_file,read_key
from backup_connection import connection_env
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage',required=True,type=Path);parser.add_argument('--files',required=True,type=Path)
    parser.add_argument('--key-file',required=True,type=Path);parser.add_argument('--allow-archive-disabled',action='store_true',help='Isolated test only: physical backup without claiming PITR.')
    args=parser.parse_args();dsn=os.environ.get('BASEBACKUP_DATABASE_URL') or os.environ.get('BACKUP_DATABASE_URL')
    if not dsn: parser.error('Set BASEBACKUP_DATABASE_URL or BACKUP_DATABASE_URL to the operator replication connection.')
    for command in ('pg_basebackup','psql'):
        if not shutil.which(command):parser.error('Install '+command+'.')
    key=read_key(args.key_file);args.storage=args.storage.resolve();args.files=args.files.resolve()
    if args.storage==args.files or args.files in args.storage.parents:parser.error('Recovery storage must be outside durable application files.')
    if not args.files.is_dir():parser.error('Durable files directory must exist.')
    env=connection_env(dsn)
    def query(sql): return subprocess.run(['psql','-X','-At','-c',sql],env=env,check=True,capture_output=True,text=True).stdout.strip()
    archive_enabled=query('SHOW archive_mode') in {'on','always'}
    if not archive_enabled and not args.allow_archive_disabled:parser.error('Enable and verify continuous WAL archiving first (restart required).')
    if archive_enabled and not query('SHOW archive_command') and not query('SHOW archive_library'):
        parser.error('Configure a real archive_command or archive_library before claiming continuous recovery.')
    if query("SELECT count(*) FROM pg_tablespace WHERE pg_tablespace_location(oid)<>''")!='0':
        parser.error('This local adapter supports default tablespaces only; configure a provider backup for external tablespaces.')
    created=datetime.now(timezone.utc);name='base-'+created.strftime('%Y%m%dT%H%M%SZ')+'.tar.enc'
    args.storage.mkdir(parents=True,exist_ok=True,mode=0o700);destination=args.storage/name
    if destination.exists():parser.error('Backup timestamp already exists.')
    with tempfile.TemporaryDirectory(prefix='volleyball-physical-backup-') as work:
        staging=Path(work);data=staging/'base'
        subprocess.run(['pg_basebackup','--pgdata',str(data),'--format=plain','--wal-method=stream','--checkpoint=fast','--no-password'],env=env,check=True)
        files=[]
        for source in args.files.rglob('*'):
            if source.is_symlink():raise RuntimeError('Durable recovery files cannot contain symlinks.')
            if source.is_file():
                with source.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                files.append({'path':source.relative_to(args.files).as_posix(),'sha256':checksum,'bytes':source.stat().st_size})
        manifest={'version':1,'type':'physical-base-with-files','created_at':created.isoformat(),'postgres_version':query('SHOW server_version'),
                  'source_lsn':query('SELECT pg_current_wal_lsn()'),'files':files,'continuous_archive_configured':archive_enabled,
                  'recovery_settings':{key:int(query('SHOW '+key)) for key in ('max_connections','max_worker_processes','max_wal_senders','max_prepared_transactions','max_locks_per_transaction')}}
        (staging/'manifest.json').write_text(json.dumps(manifest,indent=2));archive=staging/'base.tar'
        with tarfile.open(archive,'w') as bundle:
            bundle.add(data,arcname='data');bundle.add(args.files,arcname='files');bundle.add(staging/'manifest.json',arcname='manifest.json')
        encrypt_file(archive,destination,key)
    status={'last_success_at':created.isoformat(),'file':name,'type':'physical-base-with-files','pitr_enabled':archive_enabled,'bytes':destination.stat().st_size}
    temporary=args.storage/'status.json.partial';temporary.write_text(json.dumps(status,indent=2));temporary.chmod(0o600);temporary.replace(args.storage/'status.json')
    print(json.dumps(status))


if __name__=='__main__':main()
