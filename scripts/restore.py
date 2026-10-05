#!/usr/bin/env python3
"""Decrypt and validate a recovery package, then restore ONLY to an isolated DB.

Requires RESTORE_DATABASE_URL, RESTORE_ISOLATED=yes, age, pg_restore; target DB
must be pre-created and empty. Never run this against the primary database.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from backup_connection import connection_env
from dotenv import load_dotenv


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--files", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    target = os.environ.get("RESTORE_DATABASE_URL")
    live = os.environ.get("DATABASE_URL", "")
    if os.environ.get("RESTORE_ISOLATED") != "yes" or not target:
        parser.error("Set RESTORE_ISOLATED=yes and an isolated RESTORE_DATABASE_URL.")
    if live and target == live:
        parser.error("Restore and live database URLs must differ.")
    if args.files.exists() and any(args.files.iterdir()):
        parser.error("Restore files destination must be empty.")
    for command in ("age", "pg_restore", "psql"):
        if not shutil.which(command):
            parser.error(f"Install {command} first.")
    env = connection_env(target)
    tables = subprocess.run(["psql", "-At", "-c", "SELECT count(*) FROM pg_tables WHERE schemaname='public'"],
                            env=env, check=True, capture_output=True, text=True).stdout.strip()
    if tables != "0":
        parser.error("Target public schema must contain no tables.")
    with tempfile.TemporaryDirectory(prefix="volleyball-restore-") as work:
        staging = Path(work)
        archive = staging / "recovery.tar"
        subprocess.run(["age", "--decrypt", "--identity", str(args.identity), "--output", str(archive), str(args.package)], check=True)
        extracted = staging / "validated"
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev():
                    raise RuntimeError("Unsafe recovery package member.")
            bundle.extractall(extracted, filter="data")
        manifest = json.loads((extracted / "manifest.json").read_text())
        if manifest.get("version") != 1 or digest(extracted / "database.dump") != manifest["database_sha256"]:
            raise RuntimeError("Recovery database integrity check failed.")
        for item in manifest["files"]:
            path = extracted / "files" / item["path"]
            if not path.resolve().is_relative_to((extracted / "files").resolve()) or digest(path) != item["sha256"]:
                raise RuntimeError("Recovery file integrity check failed.")
        subprocess.run(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                        str(extracted / "database.dump")], env=env, check=True)
        args.files.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(extracted / "files", args.files, dirs_exist_ok=True)
    result = {"restored_at": datetime.now(timezone.utc).isoformat(), "source_created_at": manifest["created_at"],
              "database_restore": "completed-isolated", "file_checksums": "passed", "file_count": len(manifest["files"]),
              "authorization_projection_device_validation": "required before recovery promotion"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
