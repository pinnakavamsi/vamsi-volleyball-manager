#!/usr/bin/env python3
"""PostgreSQL archive_command: durable encrypted WAL archive, idempotent.

Usage: python backup_wal.py SOURCE_WAL WAL_FILENAME --storage DIRECTORY --key-file KEY
Configure in the chosen isolated/local cluster; archive_mode requires a restart.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from backup_crypto import encrypt_file,decrypt_file,read_key


def valid_name(name):
    return bool(re.fullmatch(r'(?:[0-9A-F]{24}|[0-9A-F]{8}\.history|[0-9A-F]{24}\.[0-9A-F]{8}\.backup)',name))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('filename')
    parser.add_argument('--storage',required=True,type=Path);parser.add_argument('--key-file',required=True,type=Path)
    args=parser.parse_args()
    if not valid_name(args.filename): parser.error('Unexpected WAL archive filename.')
    key=read_key(args.key_file);directory=args.storage.resolve()/'wal';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination=directory/(args.filename+'.enc');checksum=hashlib.file_digest(args.source.open('rb'),'sha256').hexdigest()
    metadata=directory/(args.filename+'.json')
    if destination.exists():
        # Recover an archive acknowledged before its sidecar survived a process crash.
        with tempfile.TemporaryDirectory(prefix='volleyball-wal-check-') as work:
            recovered=Path(work)/'wal';decrypt_file(destination,recovered,key)
            with recovered.open('rb') as stream:existing=hashlib.file_digest(stream,'sha256').hexdigest()
        if existing!=checksum:raise RuntimeError('WAL filename already exists with different bytes; refusing overwrite.')
    else:encrypt_file(args.source,destination,key)
    value={'filename':args.filename,'sha256':checksum,'archived_at':datetime.now(timezone.utc).isoformat(),'bytes':args.source.stat().st_size}
    temporary=metadata.with_suffix('.json.partial');temporary.write_text(json.dumps(value));temporary.chmod(0o600);temporary.replace(metadata)
    status=args.storage/'wal-status.json';temp=status.with_suffix('.json.partial');temp.write_text(json.dumps(value));temp.chmod(0o600);temp.replace(status)


if __name__=='__main__': main()
