"""Production container listener; the host publishes it only to loopback."""
import os

bind = "0.0.0.0:5050"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = 30
graceful_timeout = 25
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
worker_tmp_dir = "/tmp"
no_control_socket = True
umask = 0o077
accesslog = None  # Redacted application route logs exclude tokens/query strings.
errorlog = "-"
capture_output = True
loglevel = "info"
# Trust is handled once in Flask ProxyFix, after Caddy replaces these headers.
forwarded_allow_ips = "127.0.0.1"


def post_worker_init(worker):
    worker.wsgi.logger.setLevel("INFO")
