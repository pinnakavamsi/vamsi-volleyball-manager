"""PostgreSQL authentication and independent organization isolation acceptance checks."""
import json
import re
import secrets
from datetime import timedelta
from pathlib import Path

import pyotp
import pytest
from flask import g
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from volleyball.auth import hash_secret
from volleyball.common import now, uuid_str
from volleyball.db import get_db, set_scope
from volleyball.models import (Audit, Court, Division, Entry, Match, Membership, Organization,
                               Stage, Team, Token, Tournament, User, Venue)

TEST_PASSWORD = "private-test-password-2026"


@pytest.fixture
def identity_case(app, tmp_path):
    suffix = secrets.token_hex(6)
    app.config.update(DEV_EMAIL_OUTBOX=True, EMAIL_OUTBOX_PATH=str(tmp_path / "identity-outbox.jsonl"))
    data = {"suffix": suffix, "password": TEST_PASSWORD}
    with app.app_context():
        db = get_db()
        assert db.bind.dialect.name == "postgresql", "A48 requires actual PostgreSQL"
        users = {}
        for name in ("shared", "peer", "scheduler", "existing", "platform"):
            user = User(id=uuid_str(), email=f"{name}-{suffix}@example.test", name=f"Test {name}",
                        active=True, platform_admin=name == "platform", password_hash=generate_password_hash(TEST_PASSWORD, method="scrypt"))
            db.add(user)
            users[name] = user
            data[name + "_email"], data[name + "_user_id"] = user.email, user.id
        org_a = Organization(id=uuid_str(), name="Identity test A · Demo", slug=f"identity-a-{suffix}", demo=True,
                             settings={"require_mfa": False}, active=True)
        org_b = Organization(id=uuid_str(), name="Identity test B · Demo", slug=f"identity-b-{suffix}", demo=True,
                             settings={"require_mfa": False}, active=True)
        db.add_all([org_a, org_b])
        db.flush()
        data.update(org_a=org_a.id, org_b=org_b.id)
        events = {}
        for org, label in ((org_a, "a"), (org_b, "b")):
            set_scope(org.id)
            event = Tournament(id=uuid_str(), organization_id=org.id, name=f"Test event {label}", slug=f"identity-event-{label}-{suffix}",
                               start_date=now().date(), end_date=now().date() + timedelta(days=1), rules={}, status="draft")
            db.add(event)
            db.flush()
            division = Division(id=uuid_str(), organization_id=org.id, tournament_id=event.id, name="Open")
            db.add(division)
            db.flush()
            stage = Stage(id=uuid_str(), organization_id=org.id, tournament_id=event.id, division_id=division.id, name="Round robin")
            team = Team(id=uuid_str(), organization_id=org.id, name=f"Private team {label}", contact={"email": "private@example.test"})
            venue = Venue(id=uuid_str(), organization_id=org.id, name=f"Test venue {label}", timezone="America/New_York")
            db.add_all([stage, team, venue])
            db.flush()
            entry = Entry(id=uuid_str(), organization_id=org.id, tournament_id=event.id, division_id=division.id,
                          team_id=team.id, name=team.name, color="#11b981")
            court = Court(id=uuid_str(), organization_id=org.id, venue_id=venue.id, name=f"Test court {label}")
            db.add_all([entry, court])
            db.flush()
            data.update({f"event_{label}": event.id, f"team_{label}": team.id, f"venue_{label}": venue.id,
                         f"court_{label}": court.id, f"entry_{label}": entry.id, f"stage_{label}": stage.id,
                         f"division_{label}": division.id})
            events[label] = event
        set_scope(org_a.id)
        other_event = Tournament(id=uuid_str(), organization_id=org_a.id, name="Unassigned private event", slug=f"unassigned-{suffix}",
                                 start_date=now().date(), end_date=now().date())
        db.add(other_event)
        db.flush()
        data["unassigned_event"] = other_event.id
        members = [
            Membership(id=uuid_str(), organization_id=org_a.id, user_id=users["shared"].id, role="admin", status="active"),
            Membership(id=uuid_str(), organization_id=org_b.id, user_id=users["shared"].id, role="manager", status="active"),
            Membership(id=uuid_str(), organization_id=org_a.id, user_id=users["peer"].id, role="admin", status="active"),
            Membership(id=uuid_str(), organization_id=org_a.id, user_id=users["scheduler"].id, role="scheduler", status="active",
                       tournament_ids=[events["a"].id]),
            Membership(id=uuid_str(), organization_id=org_b.id, user_id=users["existing"].id, role="viewer", status="active"),
        ]
        db.add_all(members)
        data["shared_membership_a"], data["shared_membership_b"] = members[0].id, members[1].id
        db.commit()
    return data


def sign_in(app, case, user="shared", org="a"):
    client = app.test_client()
    csrf = client.get("/api/v1/auth/me").get_json()["csrf_token"]
    response = client.post("/api/v1/auth/login", json={"email": case[user + "_email"], "password": TEST_PASSWORD},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    headers = {"X-CSRF-Token": response.get_json()["csrf_token"], "X-Organization-ID": case["org_" + org]}
    return client, headers


def development_link_token(app, subject_contains):
    path = Path(app.config["EMAIL_OUTBOX_PATH"])
    messages = [json.loads(line) for line in path.read_text().splitlines()]
    message = next(m for m in reversed(messages) if subject_contains in m["subject"])
    return re.search(r"https?://\S+#([A-Za-z0-9_-]+)", message["body"]).group(1)


def test_a01_shared_identity_scope_and_roles_are_independent(app, identity_case):
    c, h = sign_in(app, identity_case)
    me = c.get("/api/v1/auth/me").get_json()
    assert {o["id"]: o["role"] for o in me["organizations"]} == {
        identity_case["org_a"]: "admin", identity_case["org_b"]: "manager"}
    a = c.get("/api/v1/teams", headers=h)
    h["X-Organization-ID"] = identity_case["org_b"]
    b = c.get("/api/v1/teams", headers=h)
    assert a.status_code == b.status_code == 200
    assert [r["id"] for r in a.get_json()["items"]] == [identity_case["team_a"]]
    assert [r["id"] for r in b.get_json()["items"]] == [identity_case["team_b"]]
    assert c.get("/api/v1/auth/me").get_json()["user"]["id"] == identity_case["shared_user_id"]


def test_a02_guessed_foreign_ids_and_links_do_not_disclose_data(app, identity_case):
    c, h = sign_in(app, identity_case)
    assert c.patch("/api/v1/teams/" + identity_case["team_b"], json={"name": "Attack"}, headers=h).status_code == 404
    assert c.post("/api/v1/courts", json={"name": "Foreign venue", "venue_id": identity_case["venue_b"]}, headers=h).status_code == 404
    assert c.get("/api/v1/tournaments/" + identity_case["event_b"], headers=h).status_code in (403, 404)
    response = c.get("/api/v1/reports?type=matches&tournament_id=" + identity_case["event_b"], headers=h)
    assert response.status_code in (403, 404)
    assert "Private team b" not in response.get_data(as_text=True)


def test_a03_removing_membership_keeps_other_org_and_history(app, identity_case):
    shared, shared_h = sign_in(app, identity_case)
    # Record an attributed change before removal.
    assert shared.patch("/api/v1/teams/" + identity_case["team_a"], json={"name": "Historical rename"}, headers=shared_h).status_code == 200
    peer, peer_h = sign_in(app, identity_case, "peer")
    response = peer.patch("/api/v1/memberships/" + identity_case["shared_membership_a"],
                          json={"status": "removed", "reason": "Test revocation"}, headers=peer_h)
    assert response.status_code == 200, response.get_json()
    assert shared.get("/api/v1/teams", headers=shared_h).status_code == 403
    shared_h["X-Organization-ID"] = identity_case["org_b"]
    assert shared.get("/api/v1/teams", headers=shared_h).status_code == 200
    with app.app_context():
        set_scope(identity_case["org_a"])
        assert get_db().get(User, identity_case["shared_user_id"]).active
        assert get_db().scalar(select(Audit).where(Audit.actor_id == identity_case["shared_user_id"], Audit.action == "team.updated"))
        assert get_db().get(Membership, identity_case["shared_membership_b"]).status == "active"


def test_a04_scheduler_cannot_change_roles_results_or_unassigned_events(app, identity_case):
    c, h = sign_in(app, identity_case, "scheduler")
    assert c.post("/api/v1/memberships", json={"email": "attack@example.test", "role": "admin"}, headers=h).status_code == 403
    assert c.patch("/api/v1/tournaments/" + identity_case["unassigned_event"], json={"name": "Attack"}, headers=h).status_code == 403
    assert c.post("/api/v1/matches/" + uuid_str() + "/approve", json={}, headers=h).status_code == 403
    assert c.post("/api/v1/teams", json={"name": "Unauthorized"}, headers=h).status_code == 403
    assert c.get("/api/v1/tournaments/" + identity_case["unassigned_event"], headers=h).status_code == 403


def test_cookie_mutations_need_csrf_and_sessions_revoke_server_side(app, identity_case):
    c, h = sign_in(app, identity_case)
    assert c.post("/api/v1/teams", json={"name": "Missing security token"},
                  headers={"X-Organization-ID": identity_case["org_a"]}).status_code == 403
    other, other_h = sign_in(app, identity_case)
    sessions = other.get("/api/v1/auth/sessions").get_json()["items"]
    prior = next(s for s in sessions if not s["current"])
    assert other.delete("/api/v1/auth/sessions/" + prior["id"], headers=other_h).status_code == 200
    assert c.get("/api/v1/teams", headers=h).status_code == 401
    assert other.get("/api/v1/teams", headers=other_h).status_code == 200


def test_existing_identity_invitation_never_changes_other_membership(app, identity_case):
    c, h = sign_in(app, identity_case)
    response = c.post("/api/v1/memberships", json={"email": identity_case["existing_email"], "role": "results"}, headers=h)
    assert response.status_code == 201, response.get_json()
    assert "token" not in response.get_json()
    # Manual provisioning sends no email. A known token in this isolated fixture
    # continues to exercise acceptance of previously issued private email links.
    raw = secrets.token_urlsafe(32)
    with app.app_context():
        token = get_db().scalar(select(Token).where(Token.kind == "invitation",
            Token.user_id == identity_case["existing_user_id"], Token.organization_id == identity_case["org_a"]))
        token.token_hash = hash_secret(raw)
        get_db().commit()
    with app.app_context():
        token = get_db().scalar(select(Token).where(Token.token_hash == hash_secret(raw)))
        assert token and raw != token.token_hash and raw not in json.dumps(token.data)
        original = get_db().scalar(select(Membership).where(Membership.user_id == identity_case["existing_user_id"],
                                                           Membership.organization_id == identity_case["org_b"]))
        assert original.status == "active" and original.role == "viewer"
    receiver = app.test_client()
    csrf = receiver.get("/api/v1/auth/me").get_json()["csrf_token"]
    accepted = receiver.post("/api/v1/auth/invitations/accept", json={"token": raw, "password": TEST_PASSWORD},
                             headers={"X-CSRF-Token": csrf})
    assert accepted.status_code == 200, accepted.get_json()
    assert {o["id"]: o["role"] for o in accepted.get_json()["organizations"]} == {
        identity_case["org_a"]: "results", identity_case["org_b"]: "viewer"}
    replay = receiver.post("/api/v1/auth/invitations/accept", json={"token": raw, "password": TEST_PASSWORD},
                           headers={"X-CSRF-Token": accepted.get_json()["csrf_token"]})
    assert replay.status_code == 400


def test_password_reset_expiry_and_revocation(app, identity_case):
    c, h = sign_in(app, identity_case)
    assert c.post("/api/v1/auth/reset/request", json={"email": identity_case["shared_email"]}, headers=h).status_code == 200
    raw = development_link_token(app, "Reset")
    with app.app_context():
        token = get_db().scalar(select(Token).where(Token.token_hash == hash_secret(raw)))
        token.expires_at = now() - timedelta(seconds=1)
        get_db().commit()
    reset = c.post("/api/v1/auth/reset/complete", json={"token": raw, "password": "new-private-password-2026"}, headers=h)
    assert reset.status_code == 400
    with app.app_context():
        token = get_db().scalar(select(Token).where(Token.token_hash == hash_secret(raw)))
        token.expires_at = now() + timedelta(hours=1)
        get_db().commit()
    second, second_h = sign_in(app, identity_case)
    assert c.post("/api/v1/auth/reset/complete", json={"token": raw, "password": "new-private-password-2026"}, headers=h).status_code == 200
    assert second.get("/api/v1/teams", headers=second_h).status_code == 401


def test_mfa_enrollment_replay_and_single_use_recovery(app, identity_case):
    c, h = sign_in(app, identity_case)
    enrollment = c.post("/api/v1/auth/mfa/setup", json={"password": TEST_PASSWORD}, headers=h)
    assert enrollment.status_code == 200, enrollment.get_json()
    setup = enrollment.get_json()
    code = pyotp.TOTP(setup["secret"]).now()
    confirmed = c.post("/api/v1/auth/mfa/confirm", json={"enrollment_id": setup["enrollment_id"], "otp": code}, headers=h)
    assert confirmed.status_code == 200
    recovery = confirmed.get_json()["recovery_codes"][0]
    with app.app_context():
        user = get_db().get(User, identity_case["shared_user_id"])
        assert user.mfa_secret != setup["secret"]
        assert hash_secret(recovery) in user.mfa_recovery_hashes
    other = app.test_client()
    csrf = other.get("/api/v1/auth/me").get_json()["csrf_token"]
    missing = other.post("/api/v1/auth/login", json={"email": identity_case["shared_email"], "password": TEST_PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert missing.status_code == 401 and missing.get_json()["error"]["code"] == "mfa_required"
    replay = other.post("/api/v1/auth/login", json={"email": identity_case["shared_email"], "password": TEST_PASSWORD, "otp": code}, headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 401
    recovered = other.post("/api/v1/auth/login", json={"email": identity_case["shared_email"], "password": TEST_PASSWORD, "otp": recovery}, headers={"X-CSRF-Token": csrf})
    assert recovered.status_code == 200
    again = app.test_client()
    again_csrf = again.get("/api/v1/auth/me").get_json()["csrf_token"]
    used = again.post("/api/v1/auth/login", json={"email": identity_case["shared_email"], "password": TEST_PASSWORD, "otp": recovery}, headers={"X-CSRF-Token": again_csrf})
    assert used.status_code == 401


def test_team_rename_retains_event_display_snapshot(app, identity_case):
    c, h = sign_in(app, identity_case)
    assert c.patch("/api/v1/teams/" + identity_case["team_a"], json={"name": "New reusable identity name", "archived": True}, headers=h).status_code == 200
    with app.app_context():
        set_scope(identity_case["org_a"])
        entry = get_db().get(Entry, identity_case["entry_a"])
        assert entry.name == "Private team a"
        assert get_db().get(Team, identity_case["team_a"]).archived


def test_platform_administration_does_not_grant_private_customer_membership(app, identity_case):
    c, h = sign_in(app, identity_case, "platform")
    assert c.get("/api/v1/platform/organizations", headers=h).status_code == 200
    assert c.get("/api/v1/teams", headers=h).status_code == 403
    assert c.get("/api/v1/tournaments/" + identity_case["event_a"], headers=h).status_code == 403


def test_organization_required_mfa_gate_checks_verified_session(app, identity_case):
    c, h = sign_in(app, identity_case)
    peer, peer_h = sign_in(app, identity_case, "peer")
    setup = c.post("/api/v1/auth/mfa/setup", json={"password": TEST_PASSWORD}, headers=h).get_json()
    assert c.post("/api/v1/auth/mfa/confirm", json={"enrollment_id": setup["enrollment_id"],
        "otp": pyotp.TOTP(setup["secret"]).now()}, headers=h).status_code == 200
    assert c.patch("/api/v1/organizations/current", json={"settings": {"require_mfa": True}}, headers=h).status_code == 200
    assert c.get("/api/v1/teams", headers=h).status_code == 200
    denied = peer.get("/api/v1/teams", headers=peer_h)
    assert denied.status_code == 403 and denied.get_json()["error"]["code"] == "mfa_required"
    # Global profile remains reachable so that the organizer can enroll MFA.
    assert peer.get("/api/v1/auth/profile").status_code == 200
    h["X-Organization-ID"] = identity_case["org_b"]
    assert c.get("/api/v1/teams", headers=h).status_code == 200


def test_a48_rls_runtime_and_transaction_local_scope_do_not_leak_through_pool(app, identity_case):
    engine = app.extensions["db_engine"]
    with engine.begin() as conn:
        privileges = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        assert privileges == (False, False)
        conn.execute(text("SELECT set_config('app.organization_id', :scope, true)"), {"scope": identity_case["org_a"]})
        assert conn.execute(text("SELECT id::text FROM teams")).scalars().all() == [identity_case["team_a"]]
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM teams")).scalar() == 0
        conn.execute(text("SELECT set_config('app.organization_id', :scope, true)"), {"scope": identity_case["org_b"]})
        assert conn.execute(text("SELECT id::text FROM teams")).scalars().all() == [identity_case["team_b"]]


def test_worker_cleans_expired_export_limits_and_preserves_identity_tokens(app):
    from volleyball.worker import run_once
    with app.app_context():
        db = get_db()
        expired = now() - timedelta(minutes=1)
        active = now() + timedelta(minutes=1)
        cases = [
            ('expired_private_export', 'private_report_export', expired, {'throttle_key': hash_secret('private fixture')}),
            ('expired_public_export', 'public_report_export', expired, {'throttle_key': hash_secret('public fixture')}),
            ('active_export', 'private_report_export', active, {'throttle_key': hash_secret('active fixture')}),
            ('expired_reset', 'password_reset', expired, {}),
            ('expired_invitation', 'invitation', expired, {}),
        ]
        ids = {}
        for label, kind, expires_at, data in cases:
            token = Token(kind=kind, token_hash=hash_secret(secrets.token_urlsafe(32)), expires_at=expires_at, data=data)
            db.add(token)
            db.flush()
            ids[label] = token.id
        db.commit()
        assert run_once() == 0
        db.expire_all()
        remaining = set(db.scalars(select(Token.id)).all())
        assert ids['expired_private_export'] not in remaining
        assert ids['expired_public_export'] not in remaining
        assert remaining == {ids['active_export'], ids['expired_reset'], ids['expired_invitation']}
