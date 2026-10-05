# syntax=docker/dockerfile:1
FROM python:3.14.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && groupadd --gid 10001 volleyball \
    && useradd --uid 10001 --gid volleyball --no-create-home --shell /usr/sbin/nologin volleyball \
    && install -d -m 0700 -o volleyball -g volleyball /app/instance

# Explicit copies keep local credentials, databases and generated files out.
COPY volleyball/ ./volleyball/
COPY migrations/ ./migrations/
COPY alembic.ini wsgi.py ./
COPY deploy/docker/ ./deploy/docker/
COPY deploy/gunicorn.conf.py ./deploy/gunicorn.conf.py

USER 10001:10001
EXPOSE 5050
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=20s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "/app/deploy/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/deploy/docker/entrypoint.py"]
CMD ["serve"]
