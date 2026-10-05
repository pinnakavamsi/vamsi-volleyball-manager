#!/usr/bin/env bash
# Run once by the official PostgreSQL image when its data volume is empty.
set -Eeuo pipefail
umask 077

: "${POSTGRES_PASSWORD:?Bootstrap PostgreSQL password is required}"
: "${VOLLEYBALL_OWNER_PASSWORD:?Migration owner password is required}"
: "${VOLLEYBALL_APP_PASSWORD:?Runtime password is required}"
: "${VOLLEYBALL_BACKUP_PASSWORD:?Backup role password is required}"
if [[ "${POSTGRES_USER:-postgres}" != postgres || "${POSTGRES_DB:-postgres}" != postgres ]]; then
    printf '%s\n' 'Use the dedicated postgres bootstrap account and postgres bootstrap database.' >&2
    exit 1
fi

# psql variables are quoted as SQL literals; passwords are never echoed.
# Explicit PGPASSWORD supports authenticated local sockets during initialization.
PGPASSWORD="$POSTGRES_PASSWORD" psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
    --username=postgres --dbname=postgres \
    --set=owner_password="$VOLLEYBALL_OWNER_PASSWORD" \
    --set=app_password="$VOLLEYBALL_APP_PASSWORD" \
    --set=backup_password="$VOLLEYBALL_BACKUP_PASSWORD" <<'SQL'
CREATE ROLE volleyball_owner LOGIN PASSWORD :'owner_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE volleyball_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE volleyball_backup LOGIN PASSWORD :'backup_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE REPLICATION BYPASSRLS;
GRANT pg_read_all_data, pg_read_all_settings TO volleyball_backup;
CREATE DATABASE volleyball OWNER volleyball_owner;
REVOKE ALL ON DATABASE volleyball FROM PUBLIC;
GRANT CONNECT ON DATABASE volleyball TO volleyball_app, volleyball_backup;
\connect volleyball
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO volleyball_owner;
GRANT USAGE ON SCHEMA public TO volleyball_app;
SQL

printf '%s\n' 'Volleyball database and separate runtime, migration, and backup roles initialized.'
