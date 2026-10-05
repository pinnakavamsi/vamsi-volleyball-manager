#!/usr/bin/env python3
"""Encrypted, full-platform logical recovery package for LOCAL/operator use.

Requires pg_dump and age. This is not WAL archiving or a PITR implementation.
Use a separate backup-role connection and remote durable storage in production.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from backup_connection import connection_env
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", required=True, type=Path)
    parser.add_argument("--files", required=True, type=Path)
    parser.add_argument("--recipient", default=os.environ.get("BACKUP_AGE_RECIPIENT"))
    parser.add_argument("--recovery-config", type=Path)
    args = parser.parse_args()
    dsn = os.environ.get("BACKUP_DATABASE_URL")
    if not dsn or not args.recipient:
        parser.error("Set BACKUP_DATABASE_URL and BACKUP_AGE_RECIPIENT (or --recipient).")
    for command in ("pg_dump", "age"):
        if not shutil.which(command):
            parser.error(f"Install {command} first.")
    args.storage = args.storage.resolve()
    args.files = args.files.resolve()
    if args.storage == args.files or args.files in args.storage.parents:
        parser.error("Backup storage must be outside the application files directory.")
    if not args.files.is_dir():
        parser.error("The complete durable files directory must exist.")
    args.storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    created = datetime.now(timezone.utc)
    name = "volleyball-" + created.strftime("%Y%m%dT%H%M%SZ") + ".tar.age"
    target = args.storage / name
    if target.exists():
        parser.error("A backup for this second already exists; retry later.")
    with tempfile.TemporaryDirectory(prefix="volleyball-backup-") as work:
        staging = Path(work)
        dump = staging / "database.dump"
        # PGPASSWORD and service files can supply authentication without logging secrets.
        env = connection_env(dsn)
        subprocess.run(["pg_dump", "--format=custom", "--file", str(dump)], env=env, check=True)
        files = []
        for path in sorted(args.files.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("Symlinks are not permitted in recovery files.")
            if path.is_file():
                files.append({"path": path.relative_to(args.files).as_posix(), "bytes": path.stat().st_size,
                              "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest()})
        manifest = {"version": 1, "created_at": created.isoformat(), "backup_type": "logical-full-platform",
                    "database_sha256": hashlib.file_digest(dump.open("rb"), "sha256").hexdigest(),
                    "files": files, "requires_isolated_restore": True,
                    "consistency_requirement": "Quiesce file mutations during backup or use a storage snapshot."}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))
        archive = staging / "recovery.tar"
        with tarfile.open(archive, "w") as bundle:
            bundle.add(dump, arcname="database.dump")
            bundle.add(staging / "manifest.json", arcname="manifest.json")
            bundle.add(args.files, arcname="files", recursive=True)
            if args.recovery_config:
                bundle.add(args.recovery_config, arcname="recovery-config", recursive=False)
        encrypted = target.with_suffix(".age.partial")
        try:
            subprocess.run(["age", "--recipient", args.recipient, "--output", str(encrypted), str(archive)], check=True)
            os.chmod(encrypted, 0o600)
            encrypted.rename(target)
        finally:
            encrypted.unlink(missing_ok=True)
    status = {"last_success_at": created.isoformat(), "file": name, "bytes": target.stat().st_size,
              "type": "logical-full-platform", "pitr_enabled": False}
    status_temp = args.storage / "status.json.tmp"
    status_temp.write_text(json.dumps(status, indent=2))
    os.chmod(status_temp, 0o600)
    status_temp.replace(args.storage / "status.json")
    print(json.dumps(status))


if __name__ == "__main__":
    main()
