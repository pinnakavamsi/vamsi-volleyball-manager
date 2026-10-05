#!/usr/bin/env python3
"""PostgreSQL restore_command: decrypt requested archived WAL to recovery path."""
import argparse
from pathlib import Path
import sys
from backup_crypto import decrypt_file,read_key
from backup_wal import valid_name


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filename');parser.add_argument('destination',type=Path)
    parser.add_argument('--storage',required=True,type=Path);parser.add_argument('--key-file',required=True,type=Path)
    args=parser.parse_args()
    if not valid_name(args.filename):parser.error('Unexpected WAL filename.')
    source=args.storage/'wal'/(args.filename+'.enc')
    if not source.is_file():return 1
    destination=args.destination.resolve()
    if not destination.is_relative_to(Path.cwd().resolve()):parser.error('WAL recovery destination must remain inside the isolated data directory.')
    decrypt_file(source,destination,read_key(args.key_file));return 0


if __name__=='__main__':sys.exit(main())
