"""All integration tests use a real isolated PostgreSQL database and RLS runtime role."""
import os
from pathlib import Path
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from volleyball import create_app
from volleyball.db import get_db

load_dotenv(Path(__file__).resolve().parents[1]/'.env')
TEST_PASSWORD='Correct test passphrase 2026!'
TEST_EMAIL='organizer@volleyball.test'

@pytest.fixture(scope='session')
def owner_engine():
    url=os.environ.get('TEST_DATABASE_URL')
    if not url or 'test' not in url.rsplit('/',1)[-1]:pytest.fail('Set TEST_DATABASE_URL to an isolated PostgreSQL test database.')
    engine=create_engine(url)
    yield engine
    engine.dispose()

@pytest.fixture
def app(owner_engine,tmp_path):
    # Tests are serialized; TRUNCATE is exclusively against the isolated test database.
    with owner_engine.begin() as conn:
        tables=[row[0] for row in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        if tables:conn.execute(text('TRUNCATE '+','.join('"'+t+'"' for t in tables)+' CASCADE'))
    application=create_app({'TESTING':True,'APP_ENV':'test','DATABASE_URL':os.environ['TEST_RUNTIME_URL'],
        'SECRET_KEY':'test-secret-with-enough-entropy-for-local-only-123456789','SESSION_COOKIE_SECURE':False,
        'ALLOW_DEMO_SEED':True,'EMAIL_DELIVERY_MODE':'file','PRIVATE_STORAGE_PATH':str(tmp_path/'files')})
    yield application
    application.extensions['db_engine'].dispose()

@pytest.fixture
def client(app):return app.test_client()

@pytest.fixture
def db(app):
    with app.app_context():yield get_db()

@pytest.fixture
def demo(app):
    from volleyball.seed import seed_demo
    with app.app_context():
        result=seed_demo(get_db(),TEST_EMAIL,TEST_PASSWORD);get_db().commit()
        result['password']=TEST_PASSWORD
        return result

@pytest.fixture
def admin_client(client,demo):
    csrf=client.get('/api/v1/auth/me').json['csrf_token']
    response=client.post('/api/v1/auth/login',json={'email':TEST_EMAIL,'password':TEST_PASSWORD},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200,response.json
    me=client.get('/api/v1/auth/me').json
    client.csrf=me['csrf_token'];client.org_id=demo['organizations'][0]['id']
    client.api_headers={'X-CSRF-Token':client.csrf,'X-Organization-ID':client.org_id}
    return client
