"""Local review defaults. Production binds behind an operator-configured TLS proxy."""
from pathlib import Path

root=Path(__file__).resolve().parent
bind='127.0.0.1:5050'
workers=2
threads=8
timeout=30
no_control_socket=True
pidfile=str(root/'instance'/'web.pid')
umask=0o077
accesslog=None  # Application emits redacted structured API logs with request IDs.
errorlog=str(root/'instance'/'application.log')
capture_output=True
loglevel='info'

def post_worker_init(worker):
    worker.wsgi.logger.setLevel('INFO')
