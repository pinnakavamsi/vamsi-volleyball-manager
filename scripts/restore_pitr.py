#!/usr/bin/env python3
"""Prepare and optionally start an isolated PostgreSQL WAL recovery cluster.

No primary cluster is contacted or modified. Destination must be empty and
RESTORE_ISOLATED=yes. Recovery uses its own loopback port; sockets are optional.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from backup_crypto import decrypt_file,read_key


def pg_literal(value):return "'"+str(value).replace("'","''")+"'"


def recovery_settings(manifest):
    defaults={'max_connections':100,'max_worker_processes':8,'max_wal_senders':10,'max_prepared_transactions':0,'max_locks_per_transaction':64}
    configured=manifest.get('recovery_settings',{})
    if not isinstance(configured,dict):raise RuntimeError('Physical recovery settings must be a dictionary.')
    result={}
    for key,fallback in defaults.items():
        value=configured.get(key,fallback)
        if isinstance(value,bool):raise RuntimeError('Invalid PostgreSQL recovery parameter.')
        try:value=int(value)
        except (TypeError,ValueError):raise RuntimeError('Invalid PostgreSQL recovery parameter.')
        minimum=10 if key=='max_locks_per_transaction' else 1 if key=='max_connections' else 0
        if not minimum<=value<=100000:raise RuntimeError('PostgreSQL recovery parameter is outside this local adapter limit.')
        # PostgreSQL WAL recovery requires these settings to be at least the source.
        result[key]=max(fallback,value)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_package',type=Path);parser.add_argument('--storage',required=True,type=Path)
    parser.add_argument('--key-file',required=True,type=Path);parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--target-time',help='Aware ISO recovery timestamp; defaults to all available archived WAL.')
    parser.add_argument('--port',type=int,default=55439);parser.add_argument('--start',action='store_true')
    parser.add_argument('--socket-dir',type=Path,help='Optional private short Unix socket directory (absolute path under 90 bytes).')
    args=parser.parse_args()
    if os.environ.get('RESTORE_ISOLATED')!='yes':parser.error('Set RESTORE_ISOLATED=yes.')
    if not 1024<=args.port<=65535:parser.error('Choose an isolated nonprivileged port.')
    destination=args.destination.resolve()
    if destination.exists() and any(destination.iterdir()):parser.error('Recovery destination must be empty.')
    if args.target_time:
        target=datetime.fromisoformat(args.target_time.replace('Z','+00:00'))
        if target.tzinfo is None:parser.error('Recovery target time must include an offset.')
    if args.start and not shutil.which('pg_ctl'):parser.error('Install compatible PostgreSQL tools.')
    sockets=args.socket_dir.resolve() if args.socket_dir else None
    if sockets and len(str(sockets).encode())>90:parser.error('Unix socket directory path must be under 90 bytes.')
    key=read_key(args.key_file)
    with tempfile.TemporaryDirectory(prefix='volleyball-pitr-') as work:
        archive=Path(work)/'base.tar';decrypt_file(args.base_package,archive,key)
        extracted=Path(work)/'validated'
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                path=Path(member.name)
                if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk() or member.isdev():raise RuntimeError('Unsafe physical recovery package member.')
            bundle.extractall(extracted,filter='data')
        manifest=json.loads((extracted/'manifest.json').read_text())
        if manifest.get('type')!='physical-base-with-files' or manifest.get('version')!=1:raise RuntimeError('Unknown physical recovery package.')
        for item in manifest['files']:
            path=(extracted/'files'/item['path']).resolve()
            if not path.is_relative_to((extracted/'files').resolve()):raise RuntimeError('Unsafe durable file reference.')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:raise RuntimeError('Durable file checksum mismatch.')
        destination.mkdir(parents=True,mode=0o700,exist_ok=True);destination.chmod(0o700)
        shutil.copytree(extracted/'data',destination/'data');shutil.copytree(extracted/'files',destination/'files')
        (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    data=destination/'data';data.chmod(0o700)
    if sockets:sockets.mkdir(parents=True,mode=0o700,exist_ok=True);sockets.chmod(0o700)
    for name in ('postmaster.pid','standby.signal','recovery.signal','postgresql.auto.conf'):(data/name).unlink(missing_ok=True)
    # Replace source settings to prevent accidental primary paths/replication or archive writes.
    command=' '.join(shlex.quote(value) for value in [sys.executable,str(Path(__file__).with_name('restore_wal.py').resolve()),'%f','%p',
                '--storage',str(args.storage.resolve()),'--key-file',str(args.key_file.resolve())])
    configuration=["listen_addresses = '127.0.0.1'",f'port = {args.port}',f'unix_socket_directories = {pg_literal(sockets or "")}',
        "archive_mode = 'off'","wal_level = 'replica'",f'restore_command = {pg_literal(command)}',
        "recovery_target_timeline = 'latest'","logging_collector = off"]
    configuration += [f'{key} = {value}' for key,value in recovery_settings(manifest).items()]
    if args.target_time:configuration += [f'recovery_target_time = {pg_literal(target.astimezone(timezone.utc).isoformat())}',"recovery_target_action = 'pause'"]
    (data/'postgresql.conf').write_text('\n'.join(configuration)+'\n')
    (data/'pg_hba.conf').write_text(('local all all trust\n' if sockets else '')+'host all all 127.0.0.1/32 scram-sha-256\nhost all all ::1/128 scram-sha-256\n')
    (data/'recovery.signal').touch()
    report={'prepared_at':datetime.now(timezone.utc).isoformat(),'source_base_at':manifest['created_at'],'destination':str(destination),
            'port':args.port,'socket':str(sockets) if sockets else None,'target':args.target_time or 'all available archived WAL',
            'database_validation':'required after recovery','file_count':len(manifest['files']),'status':'prepared-isolated',
            'recovery_settings':recovery_settings(manifest)}
    if args.start:
        subprocess.run(['pg_ctl','-D',str(data),'-l',str(destination/'recovery.log'),'-w','start'],check=True)
        report['status']='started-isolated-validation-required'
    (destination/'restore-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
