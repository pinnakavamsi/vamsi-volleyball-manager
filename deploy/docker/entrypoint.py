"""Run web and durable worker together; stop both if either process exits."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

INSTANCE = Path("/app/instance")
STATE = INSTANCE / "runtime-state.json"
stopping = False


def stop_requested(signum, frame):
    global stopping
    stopping = True


def stop_children(children):
    for child in children.values():
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and any(child.poll() is None for child in children.values()):
        time.sleep(0.2)
    for child in children.values():
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for child in children.values():
        child.wait()


def main():
    os.umask(0o077)
    if os.geteuid() == 0:
        raise SystemExit("Application containers must run as the unprivileged application user.")
    argv = sys.argv[1:] or ["serve"]
    if argv != ["serve"]:
        # One-shot Alembic/Flask operator commands use the same immutable image.
        os.execvp(argv[0], argv)
    forbidden = (
        "MIGRATION_DATABASE_URL", "BACKUP_DATABASE_URL", "BASEBACKUP_DATABASE_URL",
        "POSTGRES_PASSWORD", "VOLLEYBALL_OWNER_PASSWORD", "VOLLEYBALL_BACKUP_PASSWORD",
    )
    if any(os.environ.get(key) for key in forbidden):
        raise SystemExit("Privileged database credentials cannot be supplied to the running app.")
    database = urlsplit(os.environ.get("DATABASE_URL", ""))
    if database.scheme != "postgresql+psycopg" or unquote(database.username or "") != "volleyball_app":
        raise SystemExit("The running app requires its dedicated volleyball_app PostgreSQL connection.")
    if os.environ.get("APP_ENV") != "production":
        raise SystemExit("Set APP_ENV=production for the server image.")
    INSTANCE.mkdir(mode=0o700, parents=True, exist_ok=True)
    INSTANCE.chmod(0o700)
    logger = logging.getLogger("container")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(INSTANCE / "supervisor.log", maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_requested)
    children = {}
    exit_code = 0
    try:
        commands = {
            "web": ["gunicorn", "--config", "/app/deploy/gunicorn.conf.py", "wsgi:app"],
            "worker": [sys.executable, "-u", "-m", "volleyball.worker"],
        }
        for name, command in commands.items():
            if stopping:
                break
            children[name] = subprocess.Popen(command, start_new_session=True)
        temporary = STATE.with_suffix(".partial")
        temporary.write_text(json.dumps({"pids": {name: child.pid for name, child in children.items()}}))
        temporary.replace(STATE)
        logger.info("web_and_worker_started")
        while not stopping:
            for name, child in children.items():
                code = child.poll()
                if code is not None:
                    logger.error("child_exited process=%s exit_code=%s", name, code)
                    exit_code = 1
                    stop_requested(None, None)
                    break
            time.sleep(0.5)
    finally:
        STATE.unlink(missing_ok=True)
        stop_children(children)
        logger.info("web_and_worker_stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
