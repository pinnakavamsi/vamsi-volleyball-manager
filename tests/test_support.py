"""Support conversations and audit boundaries on the real PostgreSQL runtime role."""
from datetime import datetime
import pytest
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from volleyball.db import get_db, set_scope
from volleyball.models import Audit, SupportCase, SupportMessage, Tournament, User


@pytest.fixture
def support_platform(app, demo):
    with app.app_context():
        user = User(email="platform-support@example.test", name="Support operator", active=True,
            platform_admin=True, password_hash=generate_password_hash(demo["password"], method="scrypt"))
        get_db().add(user)
        get_db().commit()
        user_id = user.id
    client = app.test_client()
    csrf = client.get("/api/v1/auth/me").get_json()["csrf_token"]
    response = client.post("/api/v1/auth/login", json={"email": "platform-support@example.test",
        "password": demo["password"]}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["organizations"] == []
    return client, {"X-CSRF-Token": response.get_json()["csrf_token"]}, user_id


def org_headers(client, organization_id):
    return {**client.api_headers, "X-Organization-ID": organization_id}


def open_case(client, organization_id, subject="Court assignment assistance"):
    response = client.post("/api/v1/support-cases", json={"subject": subject,
        "category": "tournament_setup", "description": "We need help assigning our tournament courts."},
        headers=org_headers(client, organization_id))
    assert response.status_code == 201, response.get_json()
    case = response.get_json()["case"]
    assert case["organization_id"] == organization_id
    assert case["status"] == "open"
    assert case["messages"] == []
    return case


def support_audits(app, organization_id):
    with app.app_context():
        set_scope(organization_id)
        return get_db().execute(select(Audit.organization_id, Audit.action, Audit.entity_id,
            Audit.actor_type, Audit.actor_id, Audit.reason, Audit.before, Audit.after)
            .where(Audit.action.like("support.%"))).all()


def test_logs_pagination_keeps_organization_changes_and_support_activity(demo, admin_client):
    organization_id = demo["organizations"][0]["id"]
    changed = admin_client.patch("/api/v1/organizations/current",
        json={"name": "Updated Hudson organization"}, headers=admin_client.api_headers)
    assert changed.status_code == 200, changed.get_json()
    case = open_case(admin_client, organization_id)
    response = admin_client.get("/api/v1/audit?page=1", headers=admin_client.api_headers)
    assert response.status_code == 200, response.get_json()
    logs = response.get_json()
    assert logs["page"] == 1
    organization_log = next(row for row in logs["rows"] if row["action"] == "organization.updated")
    assert organization_log["entity_type"] == "organization"
    assert organization_log["entity_id"] == organization_id
    assert organization_log["actor_id"] == demo["user_id"]
    assert organization_log["changes"]["after"]["name"] == "Updated Hudson organization"
    assert any(row["action"] == "support.case_opened" and row["entity_id"] == case["id"] for row in logs["rows"])
    other_logs = admin_client.get("/api/v1/audit?page=1",
        headers=org_headers(admin_client, demo["organizations"][1]["id"]))
    assert other_logs.status_code == 200, other_logs.get_json()
    assert not any(row["entity_id"] in {organization_id, case["id"]} for row in other_logs.get_json()["rows"])


def test_organization_creates_case_replies_and_records_attributed_activity(app, demo, admin_client):
    organization_id = demo["organizations"][0]["id"]
    case = open_case(admin_client, organization_id)
    response = admin_client.post(f"/api/v1/support-cases/{case['id']}/messages",
        json={"message": "The issue affects Court 2."}, headers=admin_client.api_headers)
    assert response.status_code == 200, response.get_json()
    messages = response.get_json()["case"]["messages"]
    assert len(messages) == 1
    assert messages[0]["message"] == "The issue affects Court 2."
    assert messages[0]["author_type"] == "organization"
    assert messages[0]["author_name"] == "Demo organizer"
    listed = admin_client.get("/api/v1/support-cases", headers=admin_client.api_headers)
    assert listed.status_code == 200, listed.get_json()
    assert listed.get_json()["total"] == 1
    persisted = listed.get_json()["rows"][0]["messages"]
    assert len(persisted) == len(messages)
    for recorded, immediate in zip(persisted, messages):
        assert datetime.fromisoformat(recorded['created_at']) == datetime.fromisoformat(immediate['created_at'])
        assert {key: value for key, value in recorded.items() if key != 'created_at'} == {
            key: value for key, value in immediate.items() if key != 'created_at'}
    audits = support_audits(app, organization_id)
    assert {row.action for row in audits} == {"support.case_opened", "support.message_added"}
    assert all(row.organization_id == organization_id and row.entity_id == case["id"]
        and row.actor_id == demo["user_id"] and row.actor_type == "organizer" for row in audits)


def test_organization_scope_hides_foreign_cases_and_rejects_guessed_reply_id(app, demo, admin_client):
    first_id, second_id = [org["id"] for org in demo["organizations"]]
    first = open_case(admin_client, first_id, "Hudson assistance")
    second = open_case(admin_client, second_id, "Harbor assistance")
    for organization_id, expected in ((first_id, first), (second_id, second)):
        listed = admin_client.get("/api/v1/support-cases", headers=org_headers(admin_client, organization_id))
        assert listed.status_code == 200, listed.get_json()
        assert [row["id"] for row in listed.get_json()["rows"]] == [expected["id"]]
    guessed = admin_client.post(f"/api/v1/support-cases/{second['id']}/messages",
        json={"message": "Foreign conversation attack"}, headers=org_headers(admin_client, first_id))
    assert guessed.status_code == 404
    assert guessed.get_json()["error"]["code"] == "not_found"
    with app.app_context():
        set_scope(first_id)
        assert get_db().scalar(select(SupportCase).where(SupportCase.id == second["id"])) is None
        set_scope(second_id)
        assert get_db().scalars(select(SupportMessage)).all() == []
    assert [row.action for row in support_audits(app, first_id)] == ["support.case_opened"]
    assert [row.action for row in support_audits(app, second_id)] == ["support.case_opened"]


def test_organization_staff_cannot_use_platform_support_queue(demo, admin_client):
    case = open_case(admin_client, demo["organizations"][0]["id"])
    responses = [
        admin_client.get("/api/v1/platform/support-cases", headers=admin_client.api_headers),
        admin_client.post(f"/api/v1/platform/support-cases/{case['id']}/messages",
            json={"message": "Impersonated operator"}, headers=admin_client.api_headers),
        admin_client.patch(f"/api/v1/platform/support-cases/{case['id']}",
            json={"status": "resolved"}, headers=admin_client.api_headers),
    ]
    assert all(response.status_code == 403 and response.get_json()["error"]["code"] == "forbidden"
        for response in responses)
    unchanged = admin_client.get("/api/v1/support-cases", headers=admin_client.api_headers).get_json()["rows"][0]
    assert unchanged["status"] == "open" and unchanged["messages"] == []


def test_platform_queue_spans_two_organizations_and_audits_each_view_under_rls(
        app, demo, admin_client, support_platform):
    platform, headers, platform_id = support_platform
    cases = [open_case(admin_client, org["id"], org["name"] + " assistance") for org in demo["organizations"]]
    response = platform.get("/api/v1/platform/support-cases", headers=headers)
    assert response.status_code == 200, response.get_json()
    queue = response.get_json()
    assert queue["total"] == 2
    assert {row["id"] for row in queue["rows"]} == {case["id"] for case in cases}
    assert {row["organization_name"] for row in queue["rows"]} == {org["name"] for org in demo["organizations"]}
    for case in cases:
        audits = support_audits(app, case["organization_id"])
        assert len(audits) == 2
        assert all(row.organization_id == case["organization_id"] and row.entity_id == case["id"] for row in audits)
        viewed = next(row for row in audits if row.action == "support.case_viewed")
        assert viewed.actor_type == "platform_admin" and viewed.actor_id == platform_id
        assert viewed.reason and viewed.after == {"status": "open"}


def test_platform_replies_changes_status_and_preserves_organization_conversation(
        app, demo, admin_client, support_platform):
    platform, headers, platform_id = support_platform
    organization_id = demo["organizations"][1]["id"]
    case = open_case(admin_client, organization_id)
    response = platform.post(f"/api/v1/platform/support-cases/{case['id']}/messages",
        json={"message": "Please share the court name."}, headers=headers)
    assert response.status_code == 200, response.get_json()
    replied = response.get_json()["case"]
    assert replied["status"] == "in_progress"
    assert replied["messages"][0]["author_type"] == "platform_admin"
    assert replied["messages"][0]["author_name"] == "Support operator"
    updated = platform.patch(f"/api/v1/platform/support-cases/{case['id']}",
        json={"status": "waiting_on_organization"}, headers=headers)
    assert updated.status_code == 200, updated.get_json()
    organization_reply = admin_client.post(f"/api/v1/support-cases/{case['id']}/messages",
        json={"message": "Harbor court 1."}, headers=org_headers(admin_client, organization_id))
    assert organization_reply.status_code == 200, organization_reply.get_json()
    assert organization_reply.get_json()["case"]["status"] == "open"
    assert [row["author_type"] for row in organization_reply.get_json()["case"]["messages"]] == ["platform_admin", "organization"]
    resolved = platform.patch(f"/api/v1/platform/support-cases/{case['id']}",
        json={"status": "resolved"}, headers=headers)
    assert resolved.status_code == 200, resolved.get_json()
    blocked = admin_client.post(f"/api/v1/support-cases/{case['id']}/messages",
        json={"message": "Reply after resolution"}, headers=org_headers(admin_client, organization_id))
    assert blocked.status_code == 409 and blocked.get_json()["error"]["code"] == "case_closed"
    filtered = platform.get("/api/v1/platform/support-cases?status=resolved", headers=headers)
    assert filtered.status_code == 200, filtered.get_json()
    assert [row["id"] for row in filtered.get_json()["rows"]] == [case["id"]]
    audits = support_audits(app, organization_id)
    assert all(row.organization_id == organization_id and row.entity_id == case["id"] for row in audits)
    platform_message = next(row for row in audits if row.action == "support.message_added" and row.actor_id == platform_id)
    assert platform_message.actor_type == "platform_admin" and platform_message.after == {"author_type": "platform_admin"}
    statuses = [row for row in audits if row.action == "support.case_status_changed"]
    assert len(statuses) == 2
    assert all(row.actor_type == "platform_admin" and row.actor_id == platform_id for row in statuses)
    assert {(row.before["status"], row.after["status"]) for row in statuses} == {
        ("in_progress", "waiting_on_organization"), ("open", "resolved")}
    assert support_audits(app, demo["organizations"][0]["id"]) == []


def test_platform_support_scope_never_grants_private_tournament_access(
        app, demo, admin_client, support_platform):
    platform, headers, _ = support_platform
    cases = [open_case(admin_client, org["id"]) for org in demo["organizations"]]
    private_org = demo["organizations"][1]["id"]
    with app.app_context():
        set_scope(private_org)
        private_tournament = get_db().scalar(select(Tournament).where(Tournament.visibility == "private"))
        assert private_tournament is not None
        private_id = private_tournament.id
    with app.extensions["db_engine"].begin() as conn:
        assert conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one() == (False, False)
        conn.execute(text("SELECT set_config('app.organization_id', '', true)"))
        conn.execute(text("SELECT set_config('app.platform_admin', 'true', true)"))
        assert set(conn.execute(text("SELECT id::text FROM support_cases")).scalars()) == {case["id"] for case in cases}
        assert conn.execute(text("SELECT count(*) FROM tournaments")).scalar() == 0
    assert platform.get("/api/v1/platform/support-cases", headers=headers).status_code == 200
    denied = platform.get(f"/api/v1/tournaments/{private_id}",
        headers={**headers, "X-Organization-ID": private_org})
    assert denied.status_code == 403 and denied.get_json()["error"]["code"] == "forbidden"
    with app.extensions["db_engine"].begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM support_cases")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM tournaments")).scalar() == 0
