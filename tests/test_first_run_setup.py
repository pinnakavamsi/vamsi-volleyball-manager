"""First-access owner setup against disposable PostgreSQL and its RLS runtime role."""
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from volleyball import create_app
from volleyball.auth import bootstrap_admin
from volleyball.db import get_db
from volleyball.models import Audit, AuthSession, Membership, Organization, PlatformSetup, User

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1:5050'


@pytest.fixture(scope='module')
def first_run_database():
    """Never truncate the configured application or reusable test database."""
    source = os.environ.get('TEST_DATABASE_URL')
    runtime_source = os.environ.get('TEST_RUNTIME_URL')
    if not source or not runtime_source:
        pytest.fail('Set TEST_DATABASE_URL and TEST_RUNTIME_URL for disposable PostgreSQL checks.')
    owner_url, runtime_url = make_url(source), make_url(runtime_source)
    if (owner_url.host not in {'127.0.0.1', 'localhost'}
            or runtime_url.host != owner_url.host or runtime_url.port != owner_url.port
            or 'test' not in (owner_url.database or '')):
        pytest.fail('First-access checks require matching local PostgreSQL test connections.')
    database = 'volleyball_first_run_test_' + secrets.token_hex(8)
    control = create_engine(owner_url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    try:
        with control.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    except SQLAlchemyError:
        control.dispose()
        pytest.fail('The isolated PostgreSQL test connection is unavailable. Check the test configuration; connection details are suppressed.', pytrace=False)
    owner = create_engine(owner_url.set(database=database))
    try:
        environment = {**os.environ, 'MIGRATION_DATABASE_URL': owner_url.set(database=database).render_as_string(hide_password=False)}
        result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT,
                                env=environment, capture_output=True, timeout=30)
        assert result.returncode == 0, 'Disposable database migration failed; output suppressed to protect connection details.'
        yield owner, runtime_url.set(database=database).render_as_string(hide_password=False)
    finally:
        owner.dispose()
        with control.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        control.dispose()


@pytest.fixture
def first_run_app(first_run_database, tmp_path):
    owner, runtime_url = first_run_database
    with owner.begin() as connection:
        tables = [row[0] for row in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'"))]
        connection.execute(text('TRUNCATE ' + ','.join('"' + table + '"' for table in tables) + ' CASCADE'))
    app = create_app({'TESTING': True, 'APP_ENV': 'test', 'DATABASE_URL': runtime_url,
        'SECRET_KEY': secrets.token_hex(48), 'SESSION_COOKIE_SECURE': False,
        'PUBLIC_BASE_URL': ORIGIN, 'PRIVATE_STORAGE_PATH': str(tmp_path / 'files')})
    with app.app_context():
        role = get_db().execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        assert not any(role), 'The setup endpoint must work without superuser or BYPASSRLS.'
    yield app
    app.extensions['db_engine'].dispose()


def payload(label='Owner'):
    return {'email': f'{secrets.token_hex(6)}@example.test', 'name': label,
            'password': secrets.token_urlsafe(32)}


def setup_client(app):
    client = app.test_client()
    response = client.get('/api/v1/auth/me')
    assert response.status_code == 200
    return client, {'X-CSRF-Token': response.json['csrf_token'], 'Origin': ORIGIN}


def test_empty_first_access_creates_only_chosen_owner_and_authenticated_session(first_run_app):
    client, headers = setup_client(first_run_app)
    initial = client.get('/api/v1/auth/me').json
    assert initial['setup_required'] and initial['user'] is None
    assert initial['organizations'] == initial['invitations'] == []
    assert client.get('/setup').status_code == 200
    data = payload()
    response = client.post('/api/v1/auth/setup', json=data, headers=headers)
    assert response.status_code == 201, response.json
    assert response.json['user']['email'] == data['email']
    assert response.json['user']['platform_admin'] and not response.json['user']['must_change_password']
    assert not response.json['setup_required']
    assert response.json['csrf_token'] != initial['csrf_token']
    assert response.json['organizations'] == response.json['invitations'] == []
    current = client.get('/api/v1/auth/me').json
    assert current['user']['id'] == response.json['user']['id']
    assert not current['setup_required']
    organizations = client.get('/api/v1/platform/organizations')
    assert organizations.status_code == 200 and organizations.json['items'] == []
    operations = client.get('/api/v1/operations')
    assert operations.status_code == 200 and operations.json['organizations'] == 0
    cookie = response.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    with first_run_app.app_context():
        db = get_db()
        owner = db.scalar(select(User))
        assert owner.name == data['name'] and owner.active
        assert owner.password_hash != data['password'] and check_password_hash(owner.password_hash, data['password'])
        assert db.get(PlatformSetup, 1)
        assert len(list(db.scalars(select(AuthSession)))) == 1
        assert not list(db.scalars(select(Organization))) and not list(db.scalars(select(Membership)))
        db.info['platform_admin'] = True
        db.execute(text("SELECT set_config('app.platform_admin','true',true)"))
        audit = list(db.scalars(select(Audit)))
        assert len(audit) == 1 and audit[0].action == 'platform.bootstrap'
        assert data['password'] not in str(audit[0].__dict__)
    # The credentials work through the established login flow as well.
    other, other_headers = setup_client(first_run_app)
    login = other.post('/api/v1/auth/login', json={'email': data['email'], 'password': data['password']}, headers=other_headers)
    assert login.status_code == 200 and login.json['user']['platform_admin']


def test_setup_requires_csrf_and_configured_origin(first_run_app):
    client, headers = setup_client(first_run_app)
    data = payload()
    assert client.post('/api/v1/auth/setup', json=data, headers={'Origin': ORIGIN}).json['error']['code'] == 'csrf'
    for origin in (None, 'null', 'https://untrusted.example', 'http://127.0.0.1:5050.evil.example'):
        changed = {'X-CSRF-Token': headers['X-CSRF-Token']}
        if origin is not None:
            changed['Origin'] = origin
        response = client.post('/api/v1/auth/setup', json=data, headers=changed)
        assert response.status_code == 403 and response.json['error']['code'] == 'origin'
    with first_run_app.app_context():
        assert not list(get_db().scalars(select(User)))


def test_validation_preserves_empty_installation(first_run_app):
    client, headers = setup_client(first_run_app)
    data = payload()
    for changed in ({**data, 'password': 'short'}, {**data, 'password': 'a' * 257},
                    {**data, 'name': ' '}, {**data, 'email': 'invalid'}, {**data, 'sample_data': True}):
        response = client.post('/api/v1/auth/setup', json=changed, headers=headers)
        assert response.status_code == 400, response.json
    with first_run_app.app_context():
        assert not list(get_db().scalars(select(User)))
        assert not get_db().get(PlatformSetup, 1)


def test_parallel_setup_has_one_winner_and_no_partial_identities(first_run_app):
    contenders = [setup_client(first_run_app) for _ in range(2)]
    barrier = Barrier(2)
    def submit(index):
        client, headers = contenders[index]
        barrier.wait(timeout=5)
        return client.post('/api/v1/auth/setup', json=payload(f'Owner {index}'), headers=headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert next(response for response in responses if response.status_code == 409).json['error']['code'] == 'setup_complete'
    with first_run_app.app_context():
        assert len(list(get_db().scalars(select(User)))) == 1
        assert len(list(get_db().scalars(select(AuthSession)))) == 1
        assert get_db().get(PlatformSetup, 1)


def test_completed_setup_stays_closed_when_admin_is_demoted_and_inactive(first_run_app):
    client, headers = setup_client(first_run_app)
    created = client.post('/api/v1/auth/setup', json=payload(), headers=headers)
    assert created.status_code == 201
    with first_run_app.app_context():
        db = get_db()
        owner = db.scalar(select(User))
        owner.platform_admin, owner.active = False, False
        db.commit()
    visitor, visitor_headers = setup_client(first_run_app)
    assert not visitor.get('/api/v1/auth/me').json['setup_required']
    response = visitor.post('/api/v1/auth/setup', json=payload(), headers=visitor_headers)
    assert response.status_code == 409 and response.json['error']['code'] == 'setup_complete'


def test_runtime_role_cannot_clear_setup_completion(first_run_app):
    client, headers = setup_client(first_run_app)
    assert client.post('/api/v1/auth/setup', json=payload(), headers=headers).status_code == 201
    with first_run_app.app_context():
        db = get_db()
        for statement in ('DELETE FROM platform_setup', 'UPDATE platform_setup SET completed_at=now()', 'TRUNCATE platform_setup'):
            with pytest.raises(SQLAlchemyError):
                db.execute(text(statement))
            db.rollback()
            assert db.get(PlatformSetup, 1)


def test_any_administrator_creation_closes_setup_after_final_identity_removal(first_run_app):
    # No audit/session references exist in this deliberately direct migration-style
    # fixture. The user trigger, rather than web-only state, must permanently close it.
    with first_run_app.app_context():
        db = get_db()
        data = payload()
        owner = User(email=data['email'], name=data['name'], active=False, platform_admin=True,
                     password_hash=generate_password_hash(data['password'], method='scrypt'))
        db.add(owner)
        db.commit()
        assert db.get(PlatformSetup, 1)
        db.delete(owner)
        db.commit()
        assert not list(db.scalars(select(User)))
    client, headers = setup_client(first_run_app)
    assert not client.get('/api/v1/auth/me').json['setup_required']
    assert client.post('/api/v1/auth/setup', json=payload(), headers=headers).status_code == 409


def test_setup_never_overwrites_existing_identity(first_run_app):
    data = payload()
    with first_run_app.app_context():
        db = get_db()
        stored_hash = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')
        user = User(email=data['email'], name='Existing organizer', password_hash=stored_hash)
        db.add(user)
        db.commit()
        identity_id = user.id
    client, headers = setup_client(first_run_app)
    response = client.post('/api/v1/auth/setup', json=data, headers=headers)
    assert response.status_code == 409
    with first_run_app.app_context():
        user = get_db().get(User, identity_id)
        assert user.password_hash == stored_hash and user.name == 'Existing organizer' and not user.platform_admin
        assert not get_db().get(PlatformSetup, 1)


def test_failed_session_creation_rolls_back_owner_and_permanent_marker(first_run_app, monkeypatch):
    import volleyball.auth as auth
    def failing_session(user):
        raise RuntimeError('Disposable fixture failure')
    monkeypatch.setattr(auth, '_new_session', failing_session)
    client, headers = setup_client(first_run_app)
    with pytest.raises(RuntimeError, match='Disposable fixture failure'):
        client.post('/api/v1/auth/setup', json=payload(), headers=headers)
    with first_run_app.app_context():
        assert not list(get_db().scalars(select(User)))
        assert not get_db().get(PlatformSetup, 1)


def test_setup_attempts_are_rate_limited_before_validating_email(first_run_app):
    client, headers = setup_client(first_run_app)
    for _ in range(10):
        response = client.post('/api/v1/auth/setup', json={}, headers=headers)
        assert response.status_code == 400
    blocked = client.post('/api/v1/auth/setup', json=payload(), headers=headers)
    assert blocked.status_code == 429 and blocked.json['error']['code'] == 'rate_limited'
    assert client.get('/api/v1/auth/me').json['setup_required']


def test_explicit_operator_bootstrap_closes_public_setup_with_same_guard(first_run_app):
    data = payload('Operator owner')
    with first_run_app.app_context():
        db = get_db()
        owner = bootstrap_admin(db, data['email'], data['password'], data['name'])
        assert owner.platform_admin and db.get(PlatformSetup, 1)
        with pytest.raises(Exception) as error:
            bootstrap_admin(db, payload()['email'], data['password'])
        assert getattr(error.value, 'code', None) == 'setup_complete'
    visitor, headers = setup_client(first_run_app)
    assert not visitor.get('/api/v1/auth/me').json['setup_required']
    assert visitor.post('/api/v1/auth/setup', json=payload(), headers=headers).status_code == 409
