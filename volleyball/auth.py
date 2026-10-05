"""Organizer authentication, independently scoped memberships, and security controls."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from uuid import UUID

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, current_app, g, request, session
from flask_login import LoginManager, current_user, login_user, logout_user
from sqlalchemy import String, cast, func, select, text as sql_text
from werkzeug.security import check_password_hash, generate_password_hash

from .common import APIError, audit, body, iso, now, uuid_str
from .db import get_db, set_scope
from .models import Audit, AuthSession, Membership, Organization, Token, User

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
login_manager = LoginManager()
PERMISSIONS = {
    "admin": {"admin", "manage", "teams", "schedule", "results", "read"},
    "manager": {"manage", "teams", "schedule", "results", "read"},
    "scheduler": {"schedule", "read"},
    "results": {"results", "read"},
    "viewer": {"read"},
}


def hash_secret(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cipher():
    key = current_app.config.get("MFA_ENCRYPTION_KEY")
    if not key:
        # Local-development fallback; production configuration requires its own key.
        key = base64.urlsafe_b64encode(hashlib.sha256(
            (current_app.config["SECRET_KEY"] + ":volleyball-identity-v1").encode()
        ).digest())
    return Fernet(key)


def encrypt_secret(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt_secret(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise APIError("Security key configuration changed; contact the platform administrator.", 503, "security_configuration")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def valid_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise APIError("A valid identifier is required.")


def normalize_email(value):
    value = str(value or "").strip().casefold()
    if len(value) > 254 or value.count("@") != 1 or any(c.isspace() for c in value):
        raise APIError("Enter a valid email address.")
    local, domain = value.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise APIError("Enter a valid email address.")
    return value


def validate_password(value):
    if not isinstance(value, str) or len(value) < 12 or len(value) > 256:
        raise APIError("Use a password between 12 and 256 characters.")
    return value


def user_json(user):
    return {
        "id": user.id, "email": user.email, "name": user.name,
        "platform_admin": bool(user.platform_admin), "mfa_enabled": bool(user.mfa_enabled),
        "must_change_password": bool(user.password_change_required),
    }


def _load_user(user_id):
    db = get_db()
    sid = session.get("auth_session_id")
    if not sid:
        return None
    record = db.get(AuthSession, sid)
    if (not record or record.user_id != user_id or record.revoked_at or record.expires_at <= now()
            or not hmac.compare_digest(record.token_hash, hash_secret(session.get("organizer_token", "")))):
        return None
    user = db.get(User, user_id)
    if not user or not user.active:
        return None
    g.auth_session = record
    if not record.last_seen_at or (now() - record.last_seen_at).total_seconds() > 60:
        record.last_seen_at = now()
    return user


def init_auth(app):
    login_manager.init_app(app)
    login_manager.user_loader(_load_user)
    app.config["DUMMY_PASSWORD_HASH"] = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")

    @login_manager.unauthorized_handler
    def unauthorized():
        raise APIError("Sign in to continue.", 401, "unauthenticated")

    @app.before_request
    def protect_cookie_mutations():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith("/api/v1/"):
            # Admission exchanges have no prior privileged cookie. After admission,
            # scorer mutations use the same signed-session CSRF value as organizers.
            if request.path == "/api/v1/referee/exchange" and not session.get("auth_session_id"):
                return
            expected = session.get("csrf_token", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                raise APIError("Refresh the page and try again; the security token is missing or expired.", 403, "csrf")


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            raise APIError("Sign in to continue.", 401, "unauthenticated")
        g.user = current_user._get_current_object()
        if g.user.password_change_required and request.endpoint not in {"auth.profile", "auth.logout"}:
            raise APIError("Change your temporary password before using your workspace.", 403, "password_change_required")
        return view(*args, **kwargs)
    return wrapped


def require_platform_admin(view):
    @wraps(view)
    @require_login
    def wrapped(*args, **kwargs):
        if not g.user.platform_admin:
            raise APIError("Platform administrator permission is required.", 403, "forbidden")
        get_db().info["platform_admin"] = True
        get_db().execute(sql_text("SELECT set_config('app.platform_admin', 'true', true)"))
        return view(*args, **kwargs)
    return wrapped


def require_org(permission="read"):
    def decorate(view):
        @wraps(view)
        @require_login
        def wrapped(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                raise APIError("Send a JSON object.")
            raw_id = request.headers.get("X-Organization-ID") or request.args.get("organization_id") or data.get("organization_id")
            if not raw_id:
                raise APIError("Choose an organization first.", 400, "organization_required")
            org_id = valid_id(raw_id)
            get_db().info["platform_admin"] = False
            get_db().execute(sql_text("SELECT set_config('app.platform_admin', 'false', true)"))
            set_scope(org_id)
            db = get_db()
            membership = db.scalar(select(Membership).where(
                Membership.organization_id == org_id, Membership.user_id == g.user.id,
                Membership.status == "active",
            ))
            organization = db.get(Organization, org_id)
            if not membership or not organization or not organization.active:
                raise APIError("This organization is unavailable or your membership is inactive.", 403, "forbidden")
            if permission not in PERMISSIONS.get(membership.role, set()):
                raise APIError("Your organization role does not allow this operation.", 403, "forbidden")
            if (organization.settings or {}).get("require_mfa") and (not g.user.mfa_enabled or not g.auth_session.mfa_verified):
                raise APIError("This organization requires MFA. Enroll it in Profile and security.", 403, "mfa_required")
            g.organization, g.membership = organization, membership
            return view(*args, **kwargs)
        return wrapped
    return decorate


def authorize_tournament(tournament):
    if not tournament or tournament.organization_id != g.organization.id:
        raise APIError("Tournament not found.", 404, "not_found")
    allowed = g.membership.tournament_ids
    if allowed and tournament.id not in allowed:
        raise APIError("This tournament is outside your assigned scope.", 403, "forbidden")
    return tournament


def scoped_tournament_ids():
    """None means organization-wide access; a list constrains private views."""
    return list(g.membership.tournament_ids) if g.membership.tournament_ids else None


def _throttle(kind, identity, limit, minutes=10):
    db = get_db()
    key = hash_secret(kind + ":" + identity)
    # Serialize the count/insert across processes without locking other identities.
    lock_id = int(key[:16], 16) - (1 << 63)
    db.execute(sql_text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    recent = db.scalar(select(func.count()).select_from(Token).where(
        Token.kind == kind, Token.data.has_key("throttle_key"), Token.data["throttle_key"].astext == key,
        Token.expires_at > now(),
    ))
    if recent >= limit:
        raise APIError("Too many attempts. Wait before trying again.", 429, "rate_limited")
    db.add(Token(
        kind=kind, token_hash=hash_secret(secrets.token_urlsafe(32)),
        expires_at=now() + timedelta(minutes=minutes), data={"throttle_key": key},
    ))
    db.commit()


def _check_mfa(user, supplied):
    # Row locking makes recovery codes single-use and prevents concurrent OTP replay.
    get_db().refresh(user, with_for_update=True)
    value = str(supplied or "").strip().replace(" ", "")
    if not user.mfa_enabled:
        return
    if not value:
        raise APIError("Enter your authenticator code or a recovery code.", 401, "mfa_required")
    recovery = hash_secret(value.upper())
    hashes = list(user.mfa_recovery_hashes or [])
    if recovery in hashes:
        hashes.remove(recovery)
        user.mfa_recovery_hashes = hashes
        return
    totp = pyotp.TOTP(decrypt_secret(user.mfa_secret))
    counter = int(now().timestamp()) // 30
    matched = next((c for c in (counter - 1, counter, counter + 1)
                    if hmac.compare_digest(totp.at(c * 30), value)), None)
    if matched is None or (user.mfa_last_counter is not None and matched <= user.mfa_last_counter):
        raise APIError("Invalid or already used authenticator code.", 401, "invalid_credentials")
    user.mfa_last_counter = matched


def _new_session(user):
    db = get_db()
    raw_secret, new_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    previous = db.get(AuthSession, session.get("auth_session_id")) if session.get("auth_session_id") else None
    if previous:
        previous.revoked_at = now()
    record = AuthSession(
        user_id=user.id, expires_at=now() + timedelta(hours=12),
        user_agent=request.user_agent.string[:250], token_hash=hash_secret(raw_secret),
        csrf_token=new_csrf, mfa_verified=bool(user.mfa_enabled),
    )
    db.add(record)
    db.flush()
    session.clear()
    session["auth_session_id"] = record.id
    session["organizer_token"] = raw_secret
    session["csrf_token"] = new_csrf
    login_user(user, remember=False, fresh=True)
    g.user = user
    return record


def _memberships_for(user):
    db = get_db()
    rows = db.execute(select(Membership, Organization).join(
        Organization, Organization.id == Membership.organization_id
    ).where(Membership.user_id == user.id, Membership.status == "active", Organization.active.is_(True))).all()
    return [{"id": o.id, "name": o.name, "slug": o.slug, "role": m.role,
             "demo": o.demo, "requires_mfa": bool((o.settings or {}).get("require_mfa"))}
            for m, o in rows]


def _pending_invitations_for(user):
    """Return only the signed-in identity's usable invitations, without delivery secrets."""
    if not user:
        return []
    rows = get_db().execute(select(Membership, Organization, Token).join(
        Organization, Organization.id == Membership.organization_id
    ).join(Token, (Token.organization_id == Membership.organization_id)
           & (Token.user_id == Membership.user_id)
           & (Token.data["membership_id"].astext == cast(Membership.id, String))).where(
        Membership.user_id == user.id, Membership.status == "invited", Organization.active.is_(True),
        Token.kind == "invitation", Token.used_at.is_(None), Token.revoked_at.is_(None),
        Token.expires_at > now(),
    ).order_by(Membership.invited_at.desc(), Token.expires_at.desc())).all()
    invitations, seen = [], set()
    for member, organization, token in rows:
        if member.id in seen:
            continue
        seen.add(member.id)
        invitations.append({"id": member.id,
            "organization": {"id": organization.id, "name": organization.name, "slug": organization.slug},
            "role": member.role, "expires_at": iso(token.expires_at),
            "requires_mfa": bool((organization.settings or {}).get("require_mfa")),
            "delivery_status": (token.data or {}).get("delivery_status", "manual")})
    return invitations


@bp.get("/me")
def me():
    user = current_user._get_current_object() if current_user.is_authenticated else None
    organizations = _memberships_for(user) if user else []
    invitations = _pending_invitations_for(user)
    if user:
        get_db().commit()
    return {"user": user_json(user) if user else None,
            "organizations": organizations, "invitations": invitations, "csrf_token": csrf_token()}


@bp.post("/login")
def login():
    data = body()
    email = normalize_email(data.get("email"))
    _throttle("login_email", email, 10)
    _throttle("login_ip", request.remote_addr or "unknown", 50)
    db = get_db()
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    eligible = bool(user and user.active and user.password_hash)
    password_hash = user.password_hash if eligible else current_app.config["DUMMY_PASSWORD_HASH"]
    valid_password = check_password_hash(password_hash, str(data.get("password", "")))
    if not eligible or not valid_password:
        raise APIError("Email or password is incorrect.", 401, "invalid_credentials")
    _check_mfa(user, data.get("otp"))
    _new_session(user)
    audit("identity.login", "user", user.id)
    db.commit()
    return {"user": user_json(user), "organizations": _memberships_for(user),
            "invitations": _pending_invitations_for(user), "csrf_token": csrf_token()}


@bp.post("/logout")
@require_login
def logout():
    g.auth_session.revoked_at = now()
    audit("identity.logout", "auth_session", g.auth_session.id)
    get_db().commit()
    logout_user()
    session.clear()
    return {"signed_out": True, "csrf_token": csrf_token()}


def issue_token(kind, *, user_id=None, organization_id=None, email=None, meta=None, hours=24):
    raw = secrets.token_urlsafe(32)
    token = Token(kind=kind, token_hash=hash_secret(raw), user_id=user_id,
                  organization_id=organization_id, email=email, data=meta or {},
                  expires_at=now() + timedelta(hours=hours))
    get_db().add(token)
    return token, raw


def queue_identity_email(token, raw, subject, path):
    """Keep delivery material encrypted in PostgreSQL and expose no raw API token."""
    url = current_app.config.get("PUBLIC_BASE_URL", "http://127.0.0.1:5050").rstrip("/") + path + "#" + raw
    token.data = {**(token.data or {}), "delivery": encrypt_secret(json.dumps({
        "to": token.email, "subject": subject,
        "body": f"{subject}\n\nOpen this private link before {iso(token.expires_at)}:\n{url}\n\nIf you did not request this, ignore it.\n\nDesigned and developed by Vamsi Pinnaka",
    })), "delivery_status": "pending"}


def deliver_pending_identity_emails(db=None):
    """SMTP adapter; local development uses a mode-0600, CLI-only outbox."""
    db = db or get_db()
    pending = db.scalars(select(Token).where(Token.kind.in_(["invitation", "password_reset"]),
        Token.expires_at > now(), Token.used_at.is_(None), Token.revoked_at.is_(None),
        Token.data["delivery_status"].astext == "pending").limit(100).with_for_update(skip_locked=True)).all()
    delivered = 0
    for token in pending:
        data = json.loads(decrypt_secret(token.data["delivery"]))
        try:
            host = current_app.config.get("SMTP_HOST")
            if host:
                message = EmailMessage()
                message["From"] = current_app.config.get("SMTP_FROM", "volleyball@localhost")
                message["To"], message["Subject"] = data["to"], data["subject"]
                message.set_content(data["body"])
                with smtplib.SMTP(host, int(current_app.config.get("SMTP_PORT", 587)), timeout=10) as smtp:
                    if current_app.config.get("SMTP_STARTTLS", True):
                        smtp.starttls()
                    username = current_app.config.get("SMTP_USERNAME") or current_app.config.get("SMTP_USER")
                    if username:
                        smtp.login(username, current_app.config.get("SMTP_PASSWORD", ""))
                    smtp.send_message(message)
                mode = "smtp"
            elif (current_app.config.get("APP_ENV") != "production" and current_app.config.get("DEV_EMAIL_OUTBOX",
                    current_app.config.get("EMAIL_DELIVERY_MODE") == "file")):
                destination = Path(current_app.config.get("EMAIL_OUTBOX_PATH", Path(current_app.instance_path) / "email-outbox.jsonl"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "a") as output:
                    output.write(json.dumps({**data, "created_at": iso(now()), "development_only": True}) + "\n")
                mode = "development_outbox"
            else:
                continue
        except (OSError, smtplib.SMTPException):
            current_app.logger.warning("Identity email delivery deferred", extra={"token_id": token.id})
            continue
        token.data = {**{k: v for k, v in token.data.items() if k != "delivery"},
                      "delivery_status": "delivered", "delivery_mode": mode}
        delivered += 1
    db.commit()
    return delivered


def consume_token(raw, kind, *, lock=True):
    if not isinstance(raw, str) or len(raw) > 256 or len(raw) < 20:
        raise APIError("This link is invalid or expired.", 400, "invalid_token")
    query = select(Token).where(Token.token_hash == hash_secret(raw), Token.kind == kind)
    if lock:
        query = query.with_for_update()
    token = get_db().scalar(query)
    if not token or token.used_at or token.revoked_at or token.expires_at <= now():
        raise APIError("This link is invalid or expired.", 400, "invalid_token")
    return token


@bp.post("/reset/request")
def reset_request():
    email = normalize_email(body().get("email"))
    _throttle("reset_email", email, 3, 30)
    _throttle("reset_ip", request.remote_addr or "unknown", 15, 30)
    db = get_db()
    user = db.scalar(select(User).where(User.email == email, User.active.is_(True)))
    if user:
        token, raw = issue_token("password_reset", user_id=user.id, email=email, hours=1)
        queue_identity_email(token, raw, "Reset your Volleyball Manager password", "/reset")
        db.commit()
        deliver_pending_identity_emails(db)
    return {"message": "If this address has an account, password reset instructions have been queued."}


@bp.post("/reset/complete")
def reset_complete():
    data, db = body(), get_db()
    token = consume_token(data.get("token"), "password_reset")
    user = db.get(User, token.user_id)
    if not user or not user.active:
        raise APIError("This link is invalid or expired.", 400, "invalid_token")
    _check_mfa(user, data.get("otp"))
    new_password = validate_password(data.get("password"))
    if user.password_change_required and check_password_hash(user.password_hash, new_password):
        raise APIError("Choose a new password different from your temporary password.")
    user.password_hash = generate_password_hash(new_password, method="scrypt")
    user.password_change_required = False
    token.used_at = now()
    for record in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
        record.revoked_at = now()
    g.user = user
    audit("identity.password_reset", "user", user.id)
    db.commit()
    if current_user.is_authenticated and current_user.id == user.id:
        logout_user()
        session.clear()
    return {"message": "Password updated. Sign in with your new password.", "csrf_token": csrf_token()}


@bp.post("/invitations/accept")
def accept_invitation():
    data, db = body(), get_db()
    # Discover the token first; every acceptance locks organization, user, membership,
    # then token in the same order as invitation issuance and membership updates.
    candidate = consume_token(data.get("token"), "invitation", lock=False)
    org, user, member, token = _lock_invitation(candidate.organization_id,
        candidate.data.get("membership_id"), user_id=candidate.user_id, token_id=candidate.id)
    if user.password_change_required:
        raise APIError("Sign in and change your temporary password before accepting this invitation.", 403, "password_change_required")
    if user.password_hash:
        if not (current_user.is_authenticated and current_user.id == user.id):
            if not check_password_hash(user.password_hash, str(data.get("password", ""))):
                raise APIError("Sign in to your existing organizer account to accept this invitation.", 401, "existing_identity")
            _check_mfa(user, data.get("otp"))
        elif user.mfa_enabled and not g.auth_session.mfa_verified:
            _check_mfa(user, data.get("otp"))
    else:
        user.password_hash = generate_password_hash(validate_password(data.get("password")), method="scrypt")
        user.name = str(data.get("name") or user.name).strip()[:120]
    user.active = True
    member.status, member.activated_at = "active", now()
    token.used_at = now()
    token.data = {key: value for key, value in (token.data or {}).items() if key != "delivery"}
    _new_session(user)
    g.organization, g.membership = org, member
    audit("membership.invitation_accepted", "membership", member.id)
    db.commit()
    return {"user": user_json(user), "organizations": _memberships_for(user),
            "invitations": _pending_invitations_for(user), "csrf_token": csrf_token()}


def _lock_invitation(organization_id, membership_id, *, user_id, token_id=None):
    """Validate and serialize activation with issuance, revocation, and other acceptance."""
    db = get_db()
    org = db.scalar(select(Organization).where(Organization.id == organization_id,
        Organization.active.is_(True)).with_for_update(key_share=True).execution_options(populate_existing=True))
    if not org:
        raise APIError("This invitation is no longer available.", 400, "invalid_token")
    user = db.scalar(select(User).where(User.id == user_id, User.active.is_(True))
        .with_for_update().execution_options(populate_existing=True))
    member = db.scalar(select(Membership).where(Membership.id == membership_id,
        Membership.organization_id == org.id, Membership.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True))
    if not user or not member or member.status != "invited":
        raise APIError("This invitation is no longer available.", 400, "invalid_token")
    query = select(Token).where(Token.kind == "invitation", Token.user_id == user.id,
        Token.organization_id == org.id, Token.data["membership_id"].astext == member.id,
        Token.used_at.is_(None), Token.revoked_at.is_(None), Token.expires_at > now())
    if token_id:
        query = query.where(Token.id == token_id)
    token = db.scalar(query.order_by(Token.expires_at.desc()).with_for_update()
        .execution_options(populate_existing=True))
    if not token:
        raise APIError("This invitation is no longer available.", 400, "invalid_token")
    set_scope(org.id)
    return org, user, member, token


@bp.post("/invitations/<membership_id>/accept")
@require_login
def accept_own_invitation(membership_id):
    data, db = body(), get_db()
    candidate = db.scalar(select(Membership).where(Membership.id == valid_id(membership_id),
        Membership.user_id == g.user.id, Membership.status == "invited"))
    if not candidate:
        raise APIError("This invitation is no longer available.", 400, "invalid_token")
    org, user, member, token = _lock_invitation(candidate.organization_id, candidate.id, user_id=g.user.id)
    if user.password_change_required:
        raise APIError("Change your temporary password before accepting this invitation.", 403, "password_change_required")
    if (org.settings or {}).get("require_mfa") and not user.mfa_enabled:
        raise APIError("Enroll MFA in Profile and security before joining this organization.", 403, "mfa_enrollment_required")
    if user.mfa_enabled and not g.auth_session.mfa_verified:
        _check_mfa(user, data.get("otp"))
        g.auth_session.mfa_verified = True
    member.status, member.activated_at = "active", now()
    token.used_at = now()
    token.data = {key: value for key, value in (token.data or {}).items() if key != "delivery"}
    g.user, g.organization, g.membership = user, org, member
    audit("membership.invitation_accepted", "membership", member.id,
          after={"role": member.role}, reason="Explicit acceptance by the signed-in invited identity")
    db.commit()
    return {"user": user_json(user), "organizations": _memberships_for(user),
            "invitations": _pending_invitations_for(user), "csrf_token": csrf_token(),
            "message": "Invitation accepted. Your organization workspace is ready."}


@bp.route("/profile", methods=["GET", "POST"])
@require_login
def profile():
    if request.method == "GET":
        return {"user": user_json(g.user), "recovery_codes_remaining": len(g.user.mfa_recovery_hashes or [])}
    data, db = body(), get_db()
    if "password" in data:
        db.refresh(g.user, with_for_update=True)
        if not check_password_hash(g.user.password_hash, str(data.get("current_password", ""))):
            raise APIError("Current password is incorrect.", 403, "invalid_credentials")
        new_password = validate_password(data["password"])
        if g.user.password_change_required and check_password_hash(g.user.password_hash, new_password):
            raise APIError("Choose a new password different from your temporary password.")
        _check_mfa(g.user, data.get("otp"))
        g.user.password_hash = generate_password_hash(new_password, method="scrypt")
        g.user.password_change_required = False
        for record in db.scalars(select(AuthSession).where(AuthSession.user_id == g.user.id,
                AuthSession.id != session["auth_session_id"], AuthSession.revoked_at.is_(None))):
            record.revoked_at = now()
    if "name" in data:
        value = str(data["name"]).strip()
        if not value or len(value) > 120:
            raise APIError("Enter a name of up to 120 characters.")
        g.user.name = value
    audit("identity.profile_updated", "user", g.user.id)
    db.commit()
    return {"user": user_json(g.user)}


@bp.get("/sessions")
@require_login
def sessions():
    records = get_db().scalars(select(AuthSession).where(AuthSession.user_id == g.user.id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > now()).order_by(AuthSession.created_at.desc()).limit(100)).all()
    return {"items": [{"id": r.id, "created_at": iso(r.created_at), "expires_at": iso(r.expires_at),
                       "last_seen_at": iso(r.last_seen_at), "user_agent": r.user_agent,
                       "current": r.id == session.get("auth_session_id")}
                      for r in records], "total": len(records)}


@bp.delete("/sessions/<session_id>")
@require_login
def revoke_session(session_id):
    record = get_db().get(AuthSession, valid_id(session_id))
    if not record or record.user_id != g.user.id:
        raise APIError("Session not found.", 404, "not_found")
    record.revoked_at = now()
    audit("identity.session_revoked", "auth_session", record.id)
    get_db().commit()
    if record.id == session.get("auth_session_id"):
        logout_user()
        session.clear()
    return {"revoked": True, "csrf_token": csrf_token()}


@bp.post("/mfa/setup")
@require_login
def setup_mfa():
    data = body()
    if not check_password_hash(g.user.password_hash, str(data.get("password", ""))):
        raise APIError("Confirm your current password to enroll MFA.", 403, "invalid_credentials")
    replacing = bool(g.user.mfa_enabled and data.get("replace") is True)
    if g.user.mfa_enabled and not replacing:
        raise APIError("MFA is already enrolled. Use the recovery controls to replace it.", 409, "already_enrolled")
    if replacing:
        _check_mfa(g.user, data.get("otp"))
    secret = pyotp.random_base32()
    token, _ = issue_token("mfa_enrollment", user_id=g.user.id, hours=0.25,
                           meta={"secret": encrypt_secret(secret), "replace": replacing})
    get_db().commit()
    return {"enrollment_id": token.id, "secret": secret,
            "uri": pyotp.TOTP(secret).provisioning_uri(g.user.email, issuer_name="Volleyball Manager"),
            "expires_at": iso(token.expires_at)}


@bp.post("/mfa/confirm")
@require_login
def confirm_mfa():
    data, db = body(), get_db()
    token = db.scalar(select(Token).where(Token.id == valid_id(data.get("enrollment_id"))).with_for_update())
    db.refresh(g.user, with_for_update=True)
    if g.user.mfa_enabled and not (token and token.data.get("replace")):
        raise APIError("MFA is already enrolled.", 409, "already_enrolled")
    if not token or token.kind != "mfa_enrollment" or token.user_id != g.user.id or token.used_at or token.revoked_at or token.expires_at <= now():
        raise APIError("Enrollment expired. Start MFA setup again.")
    secret = decrypt_secret(token.data["secret"])
    totp = pyotp.TOTP(secret)
    counter = int(now().timestamp()) // 30
    supplied = str(data.get("otp", ""))
    matched = next((c for c in (counter - 1, counter, counter + 1)
                    if hmac.compare_digest(totp.at(c * 30), supplied)), None)
    if matched is None:
        raise APIError("The authenticator code is incorrect.")
    codes = [secrets.token_hex(6).upper() for _ in range(10)]
    g.user.mfa_secret, g.user.mfa_enabled = encrypt_secret(secret), True
    g.user.mfa_recovery_hashes = [hash_secret(c) for c in codes]
    g.user.mfa_last_counter = matched
    token.used_at = now()
    g.auth_session.mfa_verified = True
    for record in db.scalars(select(AuthSession).where(AuthSession.user_id == g.user.id,
            AuthSession.id != g.auth_session.id, AuthSession.revoked_at.is_(None))):
        record.revoked_at = now()
    audit("identity.mfa_enrolled", "user", g.user.id)
    db.commit()
    return {"mfa_enabled": True, "recovery_codes": codes,
            "message": "Save these recovery codes privately. Each can be used once."}


@bp.post("/mfa/recovery-codes")
@require_login
def regenerate_recovery():
    data = body()
    if not g.user.mfa_enabled or not check_password_hash(g.user.password_hash, str(data.get("password", ""))):
        raise APIError("Confirm your password and MFA code.", 403, "invalid_credentials")
    _check_mfa(g.user, data.get("otp"))
    codes = [secrets.token_hex(6).upper() for _ in range(10)]
    g.user.mfa_recovery_hashes = [hash_secret(c) for c in codes]
    audit("identity.mfa_recovery_regenerated", "user", g.user.id)
    get_db().commit()
    return {"recovery_codes": codes}


@bp.post("/mfa/disable")
@require_login
def disable_mfa():
    data, db = body(), get_db()
    if not check_password_hash(g.user.password_hash, str(data.get("password", ""))):
        raise APIError("Confirm your password and MFA code.", 403, "invalid_credentials")
    _check_mfa(g.user, data.get("otp"))
    orgs = _memberships_for(g.user)
    if any(o["requires_mfa"] for o in orgs):
        raise APIError("An organization you belong to requires MFA. Replace your authenticator through recovery instead.", 409, "mfa_required")
    g.user.mfa_secret, g.user.mfa_enabled, g.user.mfa_recovery_hashes = None, False, []
    g.user.mfa_last_counter = -1
    audit("identity.mfa_disabled", "user", g.user.id)
    db.commit()
    return {"mfa_enabled": False}


def bootstrap_admin(db, email, password, name="Platform administrator"):
    """Explicit local operator bootstrap; never replace a pre-existing identity."""
    email = normalize_email(email)
    if db.scalar(select(User).where(User.platform_admin.is_(True))):
        raise APIError("A platform administrator already exists; use an audited existing administrator.", 409)
    user = db.scalar(select(User).where(User.email == email))
    if user:
        raise APIError("That identity already exists; do not replace its credentials.", 409)
    user = User(email=email, name=name, active=True, platform_admin=True,
                password_hash=generate_password_hash(validate_password(password), method="scrypt"))
    db.add(user)
    db.flush()
    g.user = user
    db.info["platform_admin"] = True
    db.execute(sql_text("SELECT set_config('app.platform_admin', 'true', true)"))
    db.add(Audit(organization_id=None, actor_id=user.id, actor_type="platform_administrator",
                 action="platform.bootstrap", entity_type="user", entity_id=user.id,
                 reason="Explicit secure operator bootstrap"))
    db.commit()
    return user
