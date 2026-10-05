"""Organization administration; all business queries retain explicit tenant scope."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, g, request
from sqlalchemy import func, or_, select

from .auth import (PERMISSIONS, deliver_pending_identity_emails, issue_token,
    normalize_email, queue_identity_email, require_org, require_platform_admin,
    scoped_tournament_ids, valid_id)
from .common import APIError, audit, body, iso, now, serialize
from .db import get_db, set_scope
from .models import (Announcement, Court, CourtAssignment, Entry, Match, Membership,
    Organization, ScorerSession, Team, Token, Tournament, User, Venue)

bp = Blueprint("admin", __name__, url_prefix="/api/v1")


def text(value, label, limit=160, required=True):
    value = str(value or "").strip()
    if (required and not value) or len(value) > limit:
        raise APIError(f"{label} is required and must be at most {limit} characters.")
    return value


def timezone(value):
    value = text(value, "Timezone", 64)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise APIError("Choose a valid IANA timezone, such as America/New_York.")
    return value


def image_url(value):
    if not value:
        return None
    value = text(value, "Logo URL", 1000)
    parsed = urlparse(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise APIError("Use a valid http/https image URL or a local uploaded image path.")
    return value


def color(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)):
        raise APIError("Choose a six-digit hexadecimal color.")
    return value


def bool_value(value, label):
    if not isinstance(value, bool):
        raise APIError(f"{label} must be true or false.")
    return value


def contact(value):
    if not isinstance(value, dict) or set(value) - {"name", "email", "phone", "website", "address"}:
        raise APIError("Contact details may contain name, email, phone, website, and address.")
    return {k: text(v, "Contact " + k, 500, False) for k, v in value.items()}


def _owned(cls, object_id, lock=False):
    query = select(cls).where(cls.id == valid_id(object_id), cls.organization_id == g.organization.id)
    if lock:
        query = query.with_for_update()
    record = get_db().scalar(query)
    if record is None:
        raise APIError("Record not found in this organization.", 404, "not_found")
    return record


def _paged(query, serializer=serialize):
    db = get_db()
    try:
        limit = min(500, max(1, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise APIError("Limit and offset must be integers.")
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    records = db.scalars(query.limit(limit).offset(offset)).all()
    return {"items": [serializer(r) for r in records], "total": total, "limit": limit, "offset": offset}


def court_json(court, assignment=None):
    result = serialize(court)
    if assignment and assignment.status == "occupied":
        state = "occupied"
    elif assignment and (assignment.expires_at is None or assignment.expires_at > now()):
        state = "reserved"
    else:
        state = "available"
        from .services.courts import check_court
        try:
            check_court(court)
        except APIError:
            state = "unavailable"
    reveal_assignment = assignment
    scopes = scoped_tournament_ids() if getattr(g, "membership", None) else None
    if assignment and scopes:
        match = get_db().get(Match, assignment.match_id)
        if not match or match.tournament_id not in scopes:
            reveal_assignment = None
    result.update(status=state, match_id=reveal_assignment.match_id if reveal_assignment else None,
                  reservation_expires_at=iso(reveal_assignment.expires_at) if reveal_assignment else None)
    return result


@bp.route("/organizations/current", methods=["GET", "PATCH"])
@require_org("read")
def organization_current():
    org = g.organization
    if request.method == "GET":
        return {"organization": serialize(org), "membership": {"role": g.membership.role, "tournament_ids": g.membership.tournament_ids}}
    if g.membership.role != "admin":
        raise APIError("Organization administrator permission is required.", 403, "forbidden")
    data, before = body(), serialize(org)
    for field in ("name", "timezone", "color", "logo", "contact"):
        if field in data:
            validator = {"name": lambda v: text(v, "Organization name"), "timezone": timezone,
                         "color": color, "logo": image_url, "contact": contact}[field]
            setattr(org, field, validator(data[field]))
    if "settings" in data:
        incoming = data["settings"]
        if not isinstance(incoming, dict) or set(incoming) - {"require_mfa", "auto_finalize", "qr_admission_minutes", "units", "retention_days"}:
            raise APIError("Unknown organization configuration. Configure MFA, result approval, QR lifetime, units, or retention.")
        settings = {**(org.settings or {})}
        for key, value in incoming.items():
            if key in {"require_mfa", "auto_finalize"}:
                value = bool_value(value, key)
            elif key in {"qr_admission_minutes", "retention_days"}:
                if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > (120 if key == "qr_admission_minutes" else 36500):
                    raise APIError(f"{key} must be a positive integer within the supported range.")
            elif key == "units" and value not in {"celsius", "fahrenheit"}:
                raise APIError("Temperature units must be celsius or fahrenheit.")
            settings[key] = value
        if settings.get("require_mfa") and not g.user.mfa_enabled:
            raise APIError("Enroll your own MFA before requiring it for the organization.")
        org.settings = settings
    audit("organization.updated", "organization", org.id, before=before, after=serialize(org))
    get_db().commit()
    return {"organization": serialize(org)}


@bp.route("/platform/organizations", methods=["GET", "POST"])
@require_platform_admin
def platform_organizations():
    db = get_db()
    if request.method == "GET":
        return _paged(select(Organization).order_by(Organization.created_at.desc()))
    data = body()
    name = text(data.get("name"), "Organization name")
    slug = str(data.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,119}", slug):
        raise APIError("Use a unique organization slug with 2–120 lowercase letters, digits, or hyphens.")
    if db.scalar(select(Organization.id).where(Organization.slug == slug)):
        raise APIError("That organization slug is already in use.", 409, "conflict")
    email = normalize_email(data.get("admin_email"))
    org = Organization(name=name, slug=slug, timezone=timezone(data.get("timezone", "America/New_York")),
        active=True, demo=False, settings={"qr_admission_minutes": 15, "auto_finalize": False, "require_mfa": False})
    db.add(org)
    db.flush()
    set_scope(org.id)
    g.organization = org
    member = _invite(email, "admin", [], data.get("admin_name"), org)
    audit("platform.organization_provisioned", "organization", org.id,
          after={"name": org.name, "initial_membership_id": member.id}, reason="Platform organization provisioning")
    db.commit()
    deliver_pending_identity_emails(db)
    return {"organization": serialize(org), "membership": _membership_json(member),
            "message": "Organization created. Its initial administrator invitation has been queued."}, 201


@bp.patch("/platform/organizations/<organization_id>")
@require_platform_admin
def platform_organization_update(organization_id):
    org = get_db().get(Organization, valid_id(organization_id))
    if not org:
        raise APIError("Organization not found.", 404, "not_found")
    data = body()
    if set(data) - {"active", "reason"}:
        raise APIError("Platform administration can change active status with a reason; customer configuration requires membership.")
    reason = text(data.get("reason"), "Reason", 500)
    before = {"active": org.active}
    org.active = bool_value(data.get("active"), "Active")
    set_scope(org.id)
    g.organization = org
    audit("platform.organization_status", "organization", org.id, before=before, after={"active": org.active}, reason=reason)
    get_db().commit()
    return {"organization": serialize(org)}


def _validate_tournament_scope(values):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 500:
        raise APIError("Tournament scope must be a list of identifiers, or an empty list for organization-wide access.")
    ids = list(dict.fromkeys(valid_id(v) for v in values))
    count = get_db().scalar(select(func.count()).select_from(Tournament).where(
        Tournament.organization_id == g.organization.id, Tournament.id.in_(ids))) if ids else 0
    if count != len(ids):
        raise APIError("One or more assigned tournaments do not belong to this organization.")
    return ids


def _membership_json(member):
    user = get_db().get(User, member.user_id)
    result = serialize(member)
    result.update(email=user.email, name=user.name, mfa_enabled=user.mfa_enabled)
    return result


def _invite(email, role, scopes, name, org):
    db = get_db()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email, name=text(name or email.split("@")[0], "Name", 120), password_hash="", active=True)
        db.add(user)
        db.flush()
    member = db.scalar(select(Membership).where(Membership.organization_id == org.id, Membership.user_id == user.id).with_for_update())
    if member and member.status == "active":
        raise APIError("This organizer already belongs to the organization. Edit the existing membership.", 409, "conflict")
    if not member:
        member = Membership(organization_id=org.id, user_id=user.id)
        db.add(member)
    member.role, member.tournament_ids, member.status = role, scopes, "invited"
    member.invited_at, member.activated_at, member.suspended_at = now(), None, None
    db.flush()
    for previous in db.scalars(select(Token).where(Token.kind == "invitation", Token.organization_id == org.id,
            Token.user_id == user.id, Token.used_at.is_(None), Token.revoked_at.is_(None))):
        previous.revoked_at = now()
    token, raw = issue_token("invitation", user_id=user.id, organization_id=org.id,
                            email=email, meta={"membership_id": member.id}, hours=72)
    queue_identity_email(token, raw, f"Join {org.name} on Volleyball Manager", "/invite")
    return member


@bp.route("/memberships", methods=["GET", "POST"])
@require_org("admin")
def memberships():
    if request.method == "GET":
        return _paged(select(Membership).where(Membership.organization_id == g.organization.id).order_by(Membership.created_at), _membership_json)
    data = body()
    role = data.get("role", "viewer")
    if role not in PERMISSIONS:
        raise APIError("Choose admin, manager, scheduler, results, or viewer.")
    scopes = _validate_tournament_scope(data.get("tournament_ids", []))
    if role == "admin" and scopes:
        raise APIError("Organization administrators need organization-wide access.")
    member = _invite(normalize_email(data.get("email")), role, scopes, data.get("name"), g.organization)
    audit("membership.invited", "membership", member.id, after={"role": role, "tournament_ids": scopes})
    get_db().commit()
    deliver_pending_identity_emails()
    return {"membership": _membership_json(member), "message": "Invitation queued; membership activates on acceptance."}, 201


@bp.patch("/memberships/<membership_id>")
@require_org("admin")
def membership_update(membership_id):
    db = get_db()
    # Lock the organization to serialize changes to the final administrator.
    db.scalar(select(Organization).where(Organization.id == g.organization.id).with_for_update())
    member = _owned(Membership, membership_id, lock=True)
    data, before = body(), serialize(member)
    role = data.get("role", member.role)
    status = data.get("status", member.status)
    if role not in PERMISSIONS or status not in {"active", "suspended", "removed", "invited"}:
        raise APIError("Choose a valid role and membership status.")
    if member.status == "invited" and status == "active":
        raise APIError("An invited organizer must accept their invitation before membership activation.")
    scopes = _validate_tournament_scope(data.get("tournament_ids", member.tournament_ids))
    if role == "admin" and scopes:
        raise APIError("Organization administrators need organization-wide access.")
    if member.role == "admin" and member.status == "active" and (role != "admin" or status != "active"):
        count = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.organization_id == g.organization.id, Membership.role == "admin", Membership.status == "active"))
        if count <= 1:
            raise APIError("Invite and activate another administrator before removing the last active administrator.", 409, "last_administrator")
    member.role, member.status, member.tournament_ids = role, status, scopes
    member.suspended_at = now() if status in {"suspended", "removed"} else None
    audit("membership.updated", "membership", member.id, before=before, after=serialize(member),
          reason=text(data.get("reason", "Organizer membership administration"), "Reason", 500))
    if status in {"removed", "suspended"}:
        for token in db.scalars(select(Token).where(Token.organization_id == g.organization.id,
                Token.user_id == member.user_id, Token.kind == "invitation", Token.used_at.is_(None))):
            token.revoked_at = now()
    db.commit()
    return {"membership": _membership_json(member)}


@bp.route("/teams", methods=["GET", "POST"])
@require_org("read")
def teams():
    if request.method == "GET":
        query = select(Team).where(Team.organization_id == g.organization.id)
        if request.args.get("archived") != "true":
            query = query.where(Team.archived.is_(False))
        if request.args.get("q"):
            query = query.where(Team.name.ilike("%" + request.args["q"][:100] + "%"))
        scopes = scoped_tournament_ids()
        if scopes:
            query = query.where(Team.id.in_(select(Entry.team_id).where(Entry.organization_id == g.organization.id, Entry.tournament_id.in_(scopes))))
        return _paged(query.order_by(Team.name))
    if "teams" not in PERMISSIONS[g.membership.role]:
        raise APIError("Your role cannot create teams.", 403, "forbidden")
    if scoped_tournament_ids():
        raise APIError("Reusable team creation requires organization-wide team permission.", 403, "forbidden")
    data = body()
    team = Team(organization_id=g.organization.id, name=text(data.get("name"), "Team name"),
                abbreviation=text(data.get("abbreviation", ""), "Abbreviation", 20, False),
                color=color(data.get("color", "#11b981")), logo=image_url(data.get("logo")), contact=contact(data.get("contact", {})))
    get_db().add(team)
    get_db().flush()
    audit("team.created", "team", team.id, after=serialize(team))
    get_db().commit()
    return {"team": serialize(team)}, 201


@bp.patch("/teams/<team_id>")
@require_org("teams")
def team_update(team_id):
    if scoped_tournament_ids():
        raise APIError("Reusable team changes require organization-wide team permission.", 403, "forbidden")
    team, data = _owned(Team, team_id, lock=True), body()
    before = serialize(team)
    for key, validator in {"name": lambda v: text(v, "Team name"), "abbreviation": lambda v: text(v, "Abbreviation", 20, False),
                           "color": color, "logo": image_url, "contact": contact, "archived": lambda v: bool_value(v, "Archived")}.items():
        if key in data:
            setattr(team, key, validator(data[key]))
    # Entry display snapshots deliberately retain historical names and colors.
    audit("team.updated", "team", team.id, before=before, after=serialize(team))
    get_db().commit()
    return {"team": serialize(team)}


@bp.route("/venues", methods=["GET", "POST"])
@require_org("read")
def venues():
    if request.method == "GET":
        return _paged(select(Venue).where(Venue.organization_id == g.organization.id).order_by(Venue.name))
    if "schedule" not in PERMISSIONS[g.membership.role]:
        raise APIError("Your role cannot create venues.", 403, "forbidden")
    venue = Venue(organization_id=g.organization.id)
    _update_venue(venue, body(), create=True)
    get_db().add(venue)
    get_db().flush()
    audit("venue.created", "venue", venue.id, after=serialize(venue))
    get_db().commit()
    return {"venue": serialize(venue)}, 201


def _update_venue(venue, data, create=False):
    prior_coordinates = (venue.latitude, venue.longitude)
    for key, validator in {"name": lambda v: text(v, "Venue name"), "address": lambda v: text(v, "Address", 1000, False),
                           "timezone": timezone, "indoor": lambda v: bool_value(v, "Indoor")}.items():
        if key in data:
            setattr(venue, key, validator(data[key]))
    if create and not venue.name:
        raise APIError("A venue name is required.")
    if create and not venue.timezone:
        venue.timezone = g.organization.timezone
    for field, maximum in (("latitude", 90), ("longitude", 180)):
        if field in data:
            value = data[field]
            if value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    raise APIError(f"{field} must be a number.")
                if not -maximum <= value <= maximum:
                    raise APIError(f"{field} is outside the valid range.")
            setattr(venue, field, value)
    if (venue.latitude is None) != (venue.longitude is None):
        raise APIError("Supply both map coordinates, or leave both empty.")
    if prior_coordinates != (venue.latitude, venue.longitude):
        venue.settings = {**(venue.settings or {}), "coordinates_verified": False}
    if "settings" in data:
        venue.settings = {**(venue.settings or {}), **_availability(data["settings"])}
    if (venue.settings or {}).get("coordinates_verified") and venue.latitude is None:
        raise APIError("Provide both map coordinates before marking the location verified.")


def _availability(value):
    if not isinstance(value, dict) or set(value) - {"opening_hours", "closures", "layout_url", "reservation_minutes", "coordinates_verified"}:
        raise APIError("Venue settings accept opening_hours, closures, layout_url, reservation_minutes, and coordinates_verified.")
    result = dict(value)
    if "coordinates_verified" in result:
        result["coordinates_verified"] = bool_value(result["coordinates_verified"], "Coordinates verified")
    if "layout_url" in result:
        result["layout_url"] = image_url(result["layout_url"])
    if "reservation_minutes" in result and (not isinstance(result["reservation_minutes"], int) or not 1 <= result["reservation_minutes"] <= 240):
        raise APIError("Reservation expiry must be 1–240 minutes.")
    if "opening_hours" in result:
        hours = result["opening_hours"]
        if not isinstance(hours, dict):
            raise APIError("Opening hours must map weekday numbers 0–6 to start/end local times.")
        for day, window in hours.items():
            if isinstance(window, list) and len(window) == 2:
                window = {"open": window[0], "close": window[1]}
                hours[day] = window
            if str(day) not in [*list("0123456"), "default"] or not isinstance(window, dict) or set(window) != {"open", "close"}:
                raise APIError("Opening hours need weekday 0–6 and open/close HH:MM local times.")
            try:
                start, end = [datetime.strptime(window[k], "%H:%M").time() for k in ("open", "close")]
            except (ValueError, TypeError):
                raise APIError("Opening times must use HH:MM.")
            if start >= end:
                raise APIError("Venue closing time must follow opening time on the same day.")
    if "closures" in result:
        if not isinstance(result["closures"], list) or len(result["closures"]) > 500:
            raise APIError("Closures must be a bounded list of start/end instants.")
        for item in result["closures"]:
            try:
                if isinstance(item, dict) and "date" in item:
                    datetime.strptime(item["date"], "%Y-%m-%d")
                    continue
                start = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(item["end"].replace("Z", "+00:00"))
                if not start.tzinfo or not end.tzinfo or start >= end:
                    raise ValueError()
            except (KeyError, ValueError, TypeError, AttributeError):
                raise APIError("Each closure needs timezone-aware start/end instants, with end after start.")
    return result


@bp.patch("/venues/<venue_id>")
@require_org("schedule")
def venue_update(venue_id):
    venue, data = _owned(Venue, venue_id, lock=True), body()
    before = serialize(venue)
    _update_venue(venue, data)
    audit("venue.updated", "venue", venue.id, before=before, after=serialize(venue))
    get_db().commit()
    return {"venue": serialize(venue)}


@bp.route("/courts", methods=["GET", "POST"])
@require_org("read")
def courts():
    if request.method == "GET":
        assignments = {r.court_id: r for r in get_db().scalars(select(CourtAssignment).where(
            CourtAssignment.organization_id == g.organization.id, CourtAssignment.released_at.is_(None)))}
        query = select(Court).where(Court.organization_id == g.organization.id).order_by(Court.name)
        if request.args.get("venue_id"):
            query = query.where(Court.venue_id == valid_id(request.args["venue_id"]))
        return _paged(query, lambda c: court_json(c, assignments.get(c.id)))
    if "schedule" not in PERMISSIONS[g.membership.role]:
        raise APIError("Your role cannot create courts.", 403, "forbidden")
    data = body()
    venue = _owned(Venue, data.get("venue_id"))
    court = Court(organization_id=g.organization.id, venue_id=venue.id, name=text(data.get("name"), "Court name", 120),
                  available=bool_value(data.get("available", True), "Available"), settings=_availability(data.get("settings", {})))
    get_db().add(court)
    get_db().flush()
    audit("court.created", "court", court.id, after=serialize(court))
    get_db().commit()
    return {"court": court_json(court)}, 201


@bp.patch("/courts/<court_id>")
@require_org("schedule")
def court_update(court_id):
    court, data = _owned(Court, court_id, lock=True), body()
    before = serialize(court)
    assignment = get_db().scalar(select(CourtAssignment).where(CourtAssignment.court_id == court.id,
        CourtAssignment.organization_id == g.organization.id, CourtAssignment.released_at.is_(None)))
    if assignment and any(key in data for key in ("available", "venue_id", "settings")):
        raise APIError("Release an unused reservation or move/end the active match before changing court availability.", 409, "court_in_use")
    if "name" in data:
        court.name = text(data["name"], "Court name", 120)
    if "venue_id" in data:
        court.venue_id = _owned(Venue, data["venue_id"]).id
    if "available" in data:
        court.available = bool_value(data["available"], "Available")
    if "settings" in data:
        court.settings = _availability(data["settings"])
    audit("court.updated", "court", court.id, before=before, after=serialize(court))
    get_db().commit()
    return {"court": court_json(court, assignment)}


@bp.get("/dashboard")
@require_org("read")
def dashboard():
    from .competition import match_json
    db, org_id = get_db(), g.organization.id
    scopes = scoped_tournament_ids()
    tournament_query = select(Tournament).where(Tournament.organization_id == org_id)
    if scopes:
        tournament_query = tournament_query.where(Tournament.id.in_(scopes))
    if request.args.get("tournament_id"):
        requested = valid_id(request.args["tournament_id"])
        if scopes and requested not in scopes:
            raise APIError("Tournament is outside your assigned scope.", 403, "forbidden")
        tournament_query = tournament_query.where(Tournament.id == requested)
    tournaments = db.scalars(tournament_query.order_by(Tournament.start_date.desc()).limit(100)).all()
    # Metric scope is never silently widened by the bounded display list.
    tournament_scope = tournament_query.with_only_columns(Tournament.id).order_by(None)
    matches_query = select(Match).where(Match.organization_id == org_id, Match.tournament_id.in_(tournament_scope))
    if request.args.get("division_id"):
        matches_query = matches_query.where(Match.division_id == valid_id(request.args["division_id"]))
    status_rows = db.execute(select(Match.status, func.count()).where(Match.id.in_(matches_query.with_only_columns(Match.id)))
                             .group_by(Match.status)).all()
    distribution = dict(status_rows)
    matches = db.scalars(matches_query.order_by(Match.planned_at.desc().nullslast()).limit(250)).all()
    teams_query = select(Team).where(Team.organization_id == org_id, Team.archived.is_(False))
    if scopes or request.args.get("tournament_id"):
        teams_query = teams_query.where(Team.id.in_(select(Entry.team_id).where(Entry.organization_id == org_id,
            Entry.tournament_id.in_(tournament_scope))))
    teams = db.scalars(teams_query.order_by(Team.name).limit(250)).all()
    venues = db.scalars(select(Venue).where(Venue.organization_id == org_id).order_by(Venue.name).limit(100)).all()
    courts = db.scalars(select(Court).where(Court.organization_id == org_id).order_by(Court.name).limit(200)).all()
    assignments = {a.court_id: a for a in db.scalars(select(CourtAssignment).where(
        CourtAssignment.organization_id == org_id, CourtAssignment.released_at.is_(None)))}
    court_data = [court_json(c, assignments.get(c.id)) for c in courts]
    metrics = {
        "tournaments": db.scalar(select(func.count()).select_from(tournament_scope.subquery())),
        "teams": db.scalar(select(func.count()).select_from(teams_query.subquery())),
        "scheduled_games": sum(distribution.get(s, 0) for s in ("scheduled", "ready", "postponed")),
        "live_games": sum(distribution.get(s, 0) for s in ("in_progress", "paused")),
        "completed_games": distribution.get("ended", 0),
        "delayed_games": db.scalar(select(func.count()).select_from(Match).where(Match.id.in_(matches_query.with_only_columns(Match.id)),
                Match.status.in_(["scheduled", "ready"]), Match.planned_at < now())),
        "occupied_courts": sum(c["status"] == "occupied" for c in court_data),
        "available_courts": sum(c["status"] == "available" for c in court_data),
        "pending_results": db.scalar(select(func.count()).select_from(Match).where(Match.id.in_(matches_query.with_only_columns(Match.id)),
            Match.result_status == "submitted")),
        "status_distribution": distribution, "match_sample_size": sum(distribution.values()),
        "definitions": {"live_games": "In-progress or paused matches", "completed_games": "Ended play, independent of result approval",
            "delayed_games": "Scheduled/ready games with a planned time before now", "pending_results": "Submitted results awaiting approval",
            "occupied_courts": "Organization court inventory: active assignments", "available_courts": "Organization court inventory: currently eligible, unoccupied courts"},
    }
    announcements = db.scalars(select(Announcement).where(Announcement.organization_id == org_id,
        or_(Announcement.tournament_id.is_(None), Announcement.tournament_id.in_(tournament_scope)),
        Announcement.publish_at <= now(), or_(Announcement.expires_at.is_(None), Announcement.expires_at > now()))
        .order_by(Announcement.publish_at.desc()).limit(50)).all()
    notifications = []
    if metrics["pending_results"]:
        notifications.append({"kind": "results", "message": f"{metrics['pending_results']} result(s) awaiting approval."})
    if metrics["delayed_games"]:
        notifications.append({"kind": "schedule", "message": f"{metrics['delayed_games']} game(s) past their planned start."})
    active_ids = [m.id for m in matches if m.status in {"in_progress", "paused"}]
    if active_ids:
        stale = db.scalars(select(ScorerSession).where(ScorerSession.organization_id == org_id,
            ScorerSession.match_id.in_(active_ids), ScorerSession.revoked_at.is_(None))).all()
        for s in stale:
            if s.last_seen_at and (now() - s.last_seen_at).total_seconds() > 30:
                notifications.append({"kind": "connection", "match_id": s.match_id,
                    "message": "Scorer disconnected; match and court remain active."})
    return {"organization": serialize(g.organization), "tournaments": [serialize(t) for t in tournaments],
            "teams": [serialize(t) for t in teams], "venues": [serialize(v) for v in venues],
            "courts": court_data, "matches": [match_json(m) for m in matches], "metrics": metrics,
            "announcements": [serialize(a) for a in announcements], "notifications": notifications,
            "generated_at": iso(now()), "bounded": {"matches": 250, "tournaments": 100}}
