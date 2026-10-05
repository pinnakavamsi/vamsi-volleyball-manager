"""Convert a configured PostgreSQL URL to libpq environment, never argv secrets."""
import os
from urllib.parse import urlsplit,unquote,parse_qs


def connection_env(dsn):
    parsed=urlsplit(dsn.replace('postgresql+psycopg://','postgresql://',1))
    if parsed.scheme not in {'postgresql','postgres'}:raise RuntimeError('Use a PostgreSQL connection URL.')
    env=dict(os.environ)
    for key,value in [('PGHOST',parsed.hostname),('PGPORT',str(parsed.port) if parsed.port else None),
                      ('PGUSER',unquote(parsed.username) if parsed.username else None),('PGPASSWORD',unquote(parsed.password) if parsed.password else None),
                      ('PGDATABASE',unquote(parsed.path.lstrip('/')) if parsed.path else None)]:
        if value is not None:env[key]=value
    mapping={'host':'PGHOST','port':'PGPORT','sslmode':'PGSSLMODE','sslrootcert':'PGSSLROOTCERT','sslcert':'PGSSLCERT','sslkey':'PGSSLKEY','connect_timeout':'PGCONNECT_TIMEOUT'}
    for key,values in parse_qs(parsed.query).items():
        if key not in mapping:raise RuntimeError('Unsupported connection URL option for this local backup adapter.')
        env[mapping[key]]=values[-1]
    return env
