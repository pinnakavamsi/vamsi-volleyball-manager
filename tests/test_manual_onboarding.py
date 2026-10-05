"""Manual credential handoff and explicit own invitation acceptance on isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import secrets
from threading import Barrier

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from volleyball.auth import encrypt_secret, hash_secret
from volleyball.common import now
from volleyball.db import get_db, set_scope
from volleyball.models import Audit, AuthSession, Membership, Organization, Token, User


PASSWORD = "Existing private test password 2026!"
NEW_PASSWORD = "A different private test password 2026!"


@pytest.fixture
def manual_case(app):
    suffix = secrets.token_hex(5)
    with app.app_context():
        db = get_db()
        platform = User(email=f"platform-{suffix}@example.test", name="Platform operator", active=True,
            platform_admin=True, password_hash=generate_password_hash(PASSWORD, method="scrypt"))
        existing = User(email=f"existing-{suffix}@example.test", name="Existing organizer", active=True,
            password_hash=generate_password_hash(PASSWORD, method="scrypt"))
        db.add_all([platform, existing])
        db.commit()
        return {"suffix": suffix, "platform_email": platform.email, "platform_id": platform.id,
                "existing_email": existing.email, "existing_id": existing.id}


def login(app, email, password=PASSWORD):
    client = app.test_client()
    csrf = client.get("/api/v1/auth/me").get_json()["csrf_token"]
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    return client, {"X-CSRF-Token": response.get_json()["csrf_token"]}


def provision(client, headers, case, email=None, label="club"):
    response = client.post("/api/v1/platform/organizations", json={
        "name": "Test volleyball club", "slug": f"{label}-{case['suffix']}",
        "admin_email": email or case["existing_email"], "admin_name": "Club administrator",
        "timezone": "America/New_York"}, headers=headers)
    assert response.status_code == 201
    return response.get_json()


def accept(client, headers, membership_id, **data):
    return client.post(f"/api/v1/auth/invitations/{membership_id}/accept", json=data, headers=headers)


def test_platform_self_invitation_requires_explicit_acceptance_and_retains_session(app, manual_case):
    client, headers = login(app, manual_case["platform_email"])
    created = provision(client, headers, manual_case, manual_case["platform_email"])
    assert "initial_credentials" not in created
    assert created["invitation_delivery"]["mode"] == "manual"
    me = client.get("/api/v1/auth/me").get_json()
    assert me["organizations"] == []
    assert len(me["invitations"]) == 1
    invitation = me["invitations"][0]
    assert invitation["id"] == created["membership"]["id"]
    assert invitation["organization"]["id"] == created["organization"]["id"]
    assert not any(key in str(invitation) for key in ("token_hash", "delivery\"", "password"))
    org_headers = {**headers, "X-Organization-ID": created["organization"]["id"]}
    assert client.get("/api/v1/organizations/current", headers=org_headers).status_code == 403
    with client.session_transaction() as cookie:
        old_sid, old_csrf = cookie["auth_session_id"], cookie["csrf_token"]
    missing_csrf = accept(client, {}, invitation["id"])
    assert missing_csrf.status_code == 403
    accepted = accept(client, headers, invitation["id"])
    assert accepted.status_code == 200
    assert accepted.get_json()["invitations"] == []
    with client.session_transaction() as cookie:
        assert cookie["auth_session_id"] == old_sid
        assert cookie["csrf_token"] == old_csrf
    assert client.get("/api/v1/organizations/current", headers=org_headers).status_code == 200
    assert accept(client, headers, invitation["id"]).status_code == 400


def test_new_initial_credentials_force_different_password_before_acceptance_or_staff_creation(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    email = f"new-{manual_case['suffix']}@example.test"
    created = provision(platform, platform_headers, manual_case, email)
    credentials = created["initial_credentials"]
    assert credentials["email"] == email and credentials["must_change_password"] is True
    assert len(credentials["password"]) >= 24
    admin, headers = login(app, email, credentials["password"])
    other, _ = login(app, email, credentials["password"])
    assert admin.get("/api/v1/auth/me").get_json()["user"]["must_change_password"] is True
    assert admin.get("/api/v1/auth/profile").status_code == 200
    org_headers = {**headers, "X-Organization-ID": created["organization"]["id"]}
    assert admin.get("/api/v1/organizations/current", headers=org_headers).get_json()["error"]["code"] == "password_change_required"
    assert accept(admin, headers, created["membership"]["id"]).status_code == 403
    assert admin.post("/api/v1/memberships", json={"email": "forbidden@example.test"}, headers=org_headers).status_code == 403
    name_only = admin.post("/api/v1/auth/profile", json={"name": "A new display name"}, headers=headers)
    assert name_only.status_code == 200 and name_only.get_json()["user"]["must_change_password"] is True
    reused = admin.post("/api/v1/auth/profile", json={"current_password": credentials["password"],
        "password": credentials["password"]}, headers=headers)
    assert reused.status_code == 400
    changed = admin.post("/api/v1/auth/profile", json={"current_password": credentials["password"],
        "password": NEW_PASSWORD}, headers=headers)
    assert changed.status_code == 200 and changed.get_json()["user"]["must_change_password"] is False
    assert other.get("/api/v1/auth/me").get_json()["user"] is None
    assert accept(admin, headers, created["membership"]["id"]).status_code == 200
    assert admin.get("/api/v1/organizations/current", headers=org_headers).status_code == 200
    staff = admin.post("/api/v1/memberships", json={"email": f"staff-{manual_case['suffix']}@example.test",
        "name": "Scheduler", "role": "scheduler"}, headers=org_headers)
    assert staff.status_code == 201 and staff.get_json()["initial_credentials"]["must_change_password"] is True
    with app.app_context():
        user = get_db().scalar(select(User).where(User.email == email))
        assert credentials["password"] not in user.password_hash
        tokens = get_db().scalars(select(Token).where(Token.kind == "invitation")).all()
        assert all("delivery" not in token.data and token.data["delivery_status"] == "manual" for token in tokens)


def test_existing_identity_credentials_and_other_memberships_are_preserved(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    first = provision(platform, platform_headers, manual_case, label="first")
    existing, headers = login(app, manual_case["existing_email"])
    assert accept(existing, headers, first["membership"]["id"]).status_code == 200
    with app.app_context():
        original_hash = get_db().get(User, manual_case["existing_id"]).password_hash
    second = provision(platform, platform_headers, manual_case, label="second")
    assert "initial_credentials" not in second
    assert accept(existing, headers, second["membership"]["id"]).status_code == 200
    me = existing.get("/api/v1/auth/me").get_json()
    assert {organization["id"] for organization in me["organizations"]} == {
        first["organization"]["id"], second["organization"]["id"]}
    with app.app_context():
        user = get_db().get(User, manual_case["existing_id"])
        assert user.password_hash == original_hash and not user.password_change_required


@pytest.mark.parametrize("invalidity", ["expired", "revoked", "used", "inactive_org", "wrong_membership"])
def test_invalid_pending_invitations_are_hidden_and_cannot_activate(app, manual_case, invalidity):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    existing, headers = login(app, manual_case["existing_email"])
    with app.app_context():
        db = get_db()
        token = db.scalar(select(Token).where(Token.kind == "invitation", Token.organization_id == created["organization"]["id"]))
        if invalidity == "expired":
            token.expires_at = now() - timedelta(seconds=1)
        elif invalidity == "revoked":
            token.revoked_at = now()
        elif invalidity == "used":
            token.used_at = now()
        elif invalidity == "inactive_org":
            db.get(Organization, created["organization"]["id"]).active = False
        else:
            token.data = {**token.data, "membership_id": manual_case["existing_id"]}
        db.commit()
    assert existing.get("/api/v1/auth/me").get_json()["invitations"] == []
    assert accept(existing, headers, created["membership"]["id"]).status_code == 400
    with app.app_context():
        assert get_db().get(Membership, created["membership"]["id"]).status == "invited"


def test_invitation_acceptance_is_bound_to_own_identity_and_valid_login(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    assert platform.get("/api/v1/auth/me").get_json()["invitations"] == []
    assert accept(platform, platform_headers, created["membership"]["id"]).status_code == 400
    anonymous = app.test_client()
    csrf = anonymous.get("/api/v1/auth/me").get_json()["csrf_token"]
    assert accept(anonymous, {"X-CSRF-Token": csrf}, created["membership"]["id"]).status_code == 401


def test_required_mfa_blocks_membership_activation_until_enrollment(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    existing, headers = login(app, manual_case["existing_email"])
    with app.app_context():
        org = get_db().get(Organization, created["organization"]["id"])
        org.settings = {"require_mfa": True}
        get_db().commit()
    assert existing.get("/api/v1/auth/me").get_json()["invitations"][0]["requires_mfa"] is True
    rejected = accept(existing, headers, created["membership"]["id"])
    assert rejected.status_code == 403 and rejected.get_json()["error"]["code"] == "mfa_enrollment_required"
    assert existing.get("/api/v1/auth/profile").status_code == 200
    with app.app_context():
        assert get_db().get(Membership, created["membership"]["id"]).status == "invited"


def test_unverified_mfa_session_requires_valid_proof_without_session_rotation(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    existing, headers = login(app, manual_case["existing_email"])
    with existing.session_transaction() as cookie:
        session_id = cookie["auth_session_id"]
    recovery = secrets.token_hex(6).upper()
    with app.app_context():
        db = get_db()
        user = db.get(User, manual_case["existing_id"])
        user.mfa_enabled = True
        user.mfa_secret = encrypt_secret("JBSWY3DPEHPK3PXP")
        user.mfa_recovery_hashes = [hash_secret(recovery)]
        db.get(AuthSession, session_id).mfa_verified = False
        db.commit()
    assert accept(existing, headers, created["membership"]["id"]).get_json()["error"]["code"] == "mfa_required"
    assert accept(existing, headers, created["membership"]["id"], otp="invalid-code").status_code == 401
    assert accept(existing, headers, created["membership"]["id"], otp=recovery).status_code == 200
    with existing.session_transaction() as cookie:
        assert cookie["auth_session_id"] == session_id
    with app.app_context():
        assert get_db().get(AuthSession, session_id).mfa_verified is True
        assert get_db().get(User, manual_case["existing_id"]).mfa_recovery_hashes == []


def test_simultaneous_own_acceptance_records_activation_once(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    first, first_headers = login(app, manual_case["existing_email"])
    second, second_headers = login(app, manual_case["existing_email"])
    barrier = Barrier(2)
    def compete(client, headers):
        barrier.wait(timeout=5)
        return accept(client, headers, created["membership"]["id"]).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, first, first_headers), pool.submit(compete, second, second_headers)]
        assert sorted(future.result(timeout=10) for future in futures) == [200, 400]
    with app.app_context():
        set_scope(created["organization"]["id"])
        assert get_db().scalar(select(func.count()).select_from(Audit).where(
            Audit.action == "membership.invitation_accepted", Audit.entity_id == created["membership"]["id"])) == 1


def test_legacy_original_initial_admin_credentials_are_issued_once_and_cannot_be_claimed_by_another_org(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    victim_email = f"legacy-{manual_case['suffix']}@example.test"
    created = provision(platform, platform_headers, manual_case, victim_email, label="legacy")
    with app.app_context():
        user = get_db().scalar(select(User).where(User.email == victim_email))
        user.password_hash, user.password_change_required = "", False
        get_db().commit()
    attacker_org = provision(platform, platform_headers, manual_case, label="attacker")
    attacker, attacker_headers = login(app, manual_case["existing_email"])
    assert accept(attacker, attacker_headers, attacker_org["membership"]["id"]).status_code == 200
    attack = attacker.post("/api/v1/memberships", json={"email": victim_email, "role": "admin"},
        headers={**attacker_headers, "X-Organization-ID": attacker_org["organization"]["id"]})
    assert attack.status_code == 409 and "initial_credentials" not in attack.get_json()
    with app.app_context():
        victim = get_db().scalar(select(User).where(User.email == victim_email))
        assert not victim.password_hash and not victim.password_change_required
        assert get_db().scalar(select(Membership).where(Membership.organization_id == attacker_org["organization"]["id"],
            Membership.user_id == victim.id)) is None
    listed = platform.get("/api/v1/platform/organizations", headers=platform_headers).get_json()["items"]
    original = next(org for org in listed if org["id"] == created["organization"]["id"])
    assert original["initial_admin"]["can_issue_credentials"] is True
    issued = platform.post(f"/api/v1/platform/organizations/{created['organization']['id']}/initial-admin-credentials",
        json={}, headers=platform_headers)
    assert issued.status_code == 201 and issued.get_json()["initial_credentials"]["email"] == victim_email
    again = platform.post(f"/api/v1/platform/organizations/{created['organization']['id']}/initial-admin-credentials",
        json={}, headers=platform_headers)
    assert again.status_code == 409 and "initial_credentials" not in again.get_json()


def test_expired_original_admin_invitation_can_be_renewed_without_changing_credentials(app, manual_case):
    platform, platform_headers = login(app, manual_case["platform_email"])
    created = provision(platform, platform_headers, manual_case)
    existing, headers = login(app, manual_case["existing_email"])
    with app.app_context():
        db = get_db()
        original_hash = db.get(User, manual_case["existing_id"]).password_hash
        old_token = db.scalar(select(Token).where(Token.kind == "invitation", Token.organization_id == created["organization"]["id"]))
        old_token.expires_at = now() - timedelta(seconds=1)
        old_token_id = old_token.id
        db.commit()
    assert existing.get("/api/v1/auth/me").get_json()["invitations"] == []
    renewed = platform.post(f"/api/v1/platform/organizations/{created['organization']['id']}/initial-admin-invitation",
        json={}, headers=platform_headers)
    assert renewed.status_code == 201 and "initial_credentials" not in renewed.get_json()
    assert accept(existing, headers, created["membership"]["id"]).status_code == 200
    with app.app_context():
        assert get_db().get(User, manual_case["existing_id"]).password_hash == original_hash
        assert get_db().get(Token, old_token_id).revoked_at is not None


def test_platform_operations_reports_actual_database_and_current_process_samples(app, manual_case):
    app.config['BACKUP_STATUS_PATH'] = None
    platform, headers = login(app, manual_case['platform_email'])
    response = platform.get('/api/v1/operations', headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data['service']['status'] == 'ready'
    assert data['service']['application'] == 'available'
    assert data['service']['database'] == 'available'
    assert data['service']['schema'] == 'available'
    assert data['performance']['database_round_trip_ms'] >= 0
    assert data['performance']['sampled_at']
    stats = data['performance']['database']
    assert stats['counters'] == 'cumulative_since_reset'
    with app.app_context():
        assert stats['name'] == get_db().bind.url.database
    for key in ('connections', 'transactions_committed', 'transactions_rolled_back', 'deadlocks', 'size_bytes'):
        assert isinstance(stats[key], int) and stats[key] >= 0
    assert stats['size_bytes'] > 0
    pool = data['database_pool']
    assert pool['scope'] == 'current_application_process'
    assert pool['size'] > 0 and pool['checked_out'] >= 1
    assert 0 <= pool['utilization_percent'] <= 100
    assert data['backup']['status'] == 'not_configured'
    assert data['jobs'] == {} and data['outbox']['pending'] == 0
    assert 'database_url' not in str(data).lower()
    assert 'password' not in str(data).lower()


def test_operations_remains_unavailable_to_organization_admins_and_anonymous_visitors(app, manual_case):
    platform, platform_headers = login(app, manual_case['platform_email'])
    created = provision(platform, platform_headers, manual_case)
    organizer, headers = login(app, manual_case['existing_email'])
    assert accept(organizer, headers, created['membership']['id']).status_code == 200
    response = organizer.get('/api/v1/operations', headers={**headers, 'X-Organization-ID': created['organization']['id']})
    assert response.status_code == 403 and response.get_json()['error']['code'] == 'forbidden'
    assert app.test_client().get('/api/v1/operations').status_code == 401


@pytest.mark.parametrize('timestamp', [None, 1, 'not-a-time', '2026-10-05T10:00:00'])
def test_unreadable_backup_timestamps_report_unavailable_without_breaking_operations(app, manual_case, tmp_path, timestamp):
    path = tmp_path / 'backup-status.json'
    path.write_text(json.dumps({'last_success_at': timestamp, 'type': 'logical-full-platform', 'pitr_enabled': False}))
    app.config['BACKUP_STATUS_PATH'] = str(path)
    platform, headers = login(app, manual_case['platform_email'])
    response = platform.get('/api/v1/operations', headers=headers)
    assert response.status_code == 200
    assert response.get_json()['backup']['status'] == 'unavailable'


def test_backup_snapshot_labels_local_success_without_claiming_pitr_or_external_verification(app, manual_case, tmp_path):
    path = tmp_path / 'backup-status.json'
    path.write_text(json.dumps({'last_success_at': now().isoformat(), 'type': 'logical-full-platform', 'pitr_enabled': False}))
    app.config['BACKUP_STATUS_PATH'] = str(path)
    platform, headers = login(app, manual_case['platform_email'])
    response = platform.get('/api/v1/operations', headers=headers)
    assert response.status_code == 200
    backup = response.get_json()['backup']
    assert backup['status'] == 'recorded_success'
    assert backup['pitr_enabled'] is False and backup['wal_archive']['status'] == 'not_configured'
    assert 'operator confirmation' in backup['message']


def test_platform_legacy_self_organization_can_receive_one_separate_staff_administrator(app, manual_case):
    platform, headers = login(app, manual_case['platform_email'])
    created = provision(platform, headers, manual_case, manual_case['platform_email'])
    org_id = created['organization']['id']
    row = platform.get('/api/v1/platform/organizations', headers=headers).get_json()['items'][0]
    assert row['can_setup_administrator'] is True
    rejected_platform = platform.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': manual_case['platform_email'], 'name': 'Platform identity'}, headers=headers)
    assert rejected_platform.status_code == 400 and rejected_platform.get_json()['error']['code'] == 'platform_identity'
    staff_email = f"separate-staff-{manual_case['suffix']}@example.test"
    response = platform.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': staff_email, 'name': 'Independent organization administrator'}, headers=headers)
    assert response.status_code == 201
    staff = response.get_json()
    assert staff['initial_credentials']['email'] == staff_email
    assert staff['membership']['role'] == 'admin' and staff['membership']['status'] == 'invited'
    assert platform.get('/api/v1/auth/me').get_json()['organizations'] == []
    row = platform.get('/api/v1/platform/organizations', headers=headers).get_json()['items'][0]
    assert row['can_setup_administrator'] is False
    assert row['initial_admin']['email'] == staff_email
    assert row['initial_admin']['membership_id'] == staff['membership']['id']
    duplicate = platform.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': f"another-{manual_case['suffix']}@example.test", 'name': 'Another administrator'}, headers=headers)
    assert duplicate.status_code == 409 and duplicate.get_json()['error']['code'] == 'administrator_exists'
    organizer, organizer_headers = login(app, staff_email, staff['initial_credentials']['password'])
    changed = organizer.post('/api/v1/auth/profile', json={'current_password': staff['initial_credentials']['password'],
        'password': NEW_PASSWORD}, headers=organizer_headers)
    assert changed.status_code == 200
    assert accept(organizer, organizer_headers, staff['membership']['id']).status_code == 200
    duplicate = platform.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': f"another-{manual_case['suffix']}@example.test", 'name': 'Another administrator'}, headers=headers)
    assert duplicate.status_code == 409
    with app.app_context():
        original = get_db().get(Membership, created['membership']['id'])
        assert original.user_id == manual_case['platform_id'] and original.status == 'invited'


def test_separate_staff_setup_preserves_existing_password_and_becomes_initial_invitation_renewal_target(app, manual_case):
    platform, headers = login(app, manual_case['platform_email'])
    created = provision(platform, headers, manual_case, manual_case['platform_email'])
    org_id = created['organization']['id']
    with app.app_context():
        original_hash = get_db().get(User, manual_case['existing_id']).password_hash
    response = platform.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': manual_case['existing_email'], 'name': 'Existing independent administrator'}, headers=headers)
    assert response.status_code == 201 and 'initial_credentials' not in response.get_json()
    staff_id = response.get_json()['membership']['id']
    with app.app_context():
        db = get_db()
        token = db.scalar(select(Token).where(Token.kind == 'invitation', Token.user_id == manual_case['existing_id'],
            Token.organization_id == org_id))
        token.expires_at = now() - timedelta(seconds=1)
        db.commit()
    renewal = platform.post(f'/api/v1/platform/organizations/{org_id}/initial-admin-invitation', json={}, headers=headers)
    assert renewal.status_code == 201 and renewal.get_json()['membership']['id'] == staff_id
    assert 'initial_credentials' not in renewal.get_json()
    with app.app_context():
        assert get_db().get(User, manual_case['existing_id']).password_hash == original_hash
        assert get_db().get(Membership, created['membership']['id']).status == 'invited'
    existing, existing_headers = login(app, manual_case['existing_email'])
    assert accept(existing, existing_headers, staff_id).status_code == 200
    forbidden = existing.post(f'/api/v1/platform/organizations/{org_id}/administrator',
        json={'email': 'forbidden@example.test', 'name': 'Forbidden target'}, headers=existing_headers)
    assert forbidden.status_code == 403
