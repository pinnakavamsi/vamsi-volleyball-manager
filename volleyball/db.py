"""Request-scoped sessions and pooled-connection safe tenant context."""
from flask import current_app, g
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session


def init_db(app):
    url = app.config['DATABASE_URL']
    if not url.startswith('postgresql'):
        raise RuntimeError('PostgreSQL is required; SQLite is not supported.')
    app.extensions['db_engine'] = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20,
        connect_args={'options': '-c statement_timeout=15000 -c lock_timeout=5000'})
    app.teardown_appcontext(close_db)

@event.listens_for(Session, 'after_begin')
def scope_after_begin(session, transaction, connection):
    scope = session.info.get('organization_id', '')
    connection.execute(text("SELECT set_config('app.organization_id', :scope, true)"), {'scope':str(scope)})
    connection.execute(text("SELECT set_config('app.platform_admin', :value, true)"), {'value':'true' if session.info.get('platform_admin') else 'false'})


def get_db():
    if 'db' not in g:
        g.db = Session(current_app.extensions['db_engine'], expire_on_commit=False)
    return g.db

def set_scope(organization_id):
    db = get_db()
    db.info['organization_id'] = str(organization_id)
    db.execute(text("SELECT set_config('app.organization_id', :scope, true)"), {'scope':str(organization_id)})

def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.rollback()
        db.close()
