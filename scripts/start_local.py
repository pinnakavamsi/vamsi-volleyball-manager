"""Prepare and run this project's local database, web application and worker."""
import signal
import socket
import subprocess
import sys
import time
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'.venv'/'bin'/'python'

def local_address():
    from dotenv import dotenv_values
    url=os.environ.get('PUBLIC_BASE_URL') or dotenv_values(ROOT/'.env').get('PUBLIC_BASE_URL') or 'http://127.0.0.1:5050'
    parsed=urlsplit(url)
    try:port=parsed.port
    except ValueError:raise SystemExit('PUBLIC_BASE_URL has an invalid local port.') from None
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or port is None or not 1024<=port<=65535:
        raise SystemExit('Local startup requires PUBLIC_BASE_URL=http://127.0.0.1:PORT or http://localhost:PORT with an unprivileged port.')
    return '127.0.0.1',port,url.rstrip('/')

def main():
    if not PYTHON.exists():raise SystemExit('Install the project .venv and requirements.txt first; see README.md.')
    host,port,url=local_address()
    with socket.socket() as probe:
        if probe.connect_ex((host,port))==0:
            raise SystemExit(f'Port {port} is already in use. Preserve that process and choose another loopback PUBLIC_BASE_URL port in this project .env.')
    for command in ([str(PYTHON),'scripts/local_database.py'],[str(ROOT/'.venv/bin/alembic'),'upgrade','head']):
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
        print(f'Volleyball Manager: {url}\nA fresh installation starts empty. Create the first platform administrator in the browser.\nPress Ctrl+C to stop web and worker; PostgreSQL stays available.',flush=True)
        while all(process.poll() is None for process in processes):time.sleep(.5)
    finally:
        stop()
        for process in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        worker_log.close()

if __name__=='__main__':main()
