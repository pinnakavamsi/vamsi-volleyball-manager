"""Prepare and run this project's local database, web application and worker."""
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'.venv'/'bin'/'python'

def main():
    if not PYTHON.exists():raise SystemExit('Install the project .venv and requirements.txt first; see README.md.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',5050))==0:
            raise SystemExit('Port 5050 is already serving an application. Open http://127.0.0.1:5050 or stop that process before starting another.')
    for command in ([str(PYTHON),'scripts/local_database.py'],[str(ROOT/'.venv/bin/alembic'),'upgrade','head'],[str(PYTHON),'scripts/prepare_demo.py']):
        subprocess.run(command,cwd=ROOT,check=True)
    processes=[]
    def stop(*_):
        for process in processes:
            if process.poll() is None:process.terminate()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    worker_log=(ROOT/'instance'/'worker.log').open('a')
    (ROOT/'instance'/'worker.log').chmod(0o600)
    application_log=ROOT/'instance'/'application.log'
    application_log.touch(mode=0o600,exist_ok=True)
    application_log.chmod(0o600)
    try:
        processes.append(subprocess.Popen([str(PYTHON),'-m','volleyball.worker'],cwd=ROOT,stdout=worker_log,stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen([str(ROOT/'.venv/bin/gunicorn'),'-c','gunicorn.conf.py','wsgi:app'],cwd=ROOT))
        print('Volleyball Manager: http://127.0.0.1:5050\nLocal login credentials: instance/demo-access.txt\nPress Ctrl+C to stop web and worker; PostgreSQL stays available.',flush=True)
        while all(process.poll() is None for process in processes):time.sleep(.5)
    finally:
        stop()
        for process in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        worker_log.close()

if __name__=='__main__':main()
