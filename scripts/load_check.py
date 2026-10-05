"""Measure the proposed 25-game/500-reader baseline in a disposable local DB.

Transport can be an isolated loopback gunicorn fixture server or Flask's
in-process WSGI client, with actual PostgreSQL commits and the nonowner RLS role.
This does not measure browser rendering, phone radios, LCP or INP. No existing
database is truncated or seeded. The existing development server is never used.

Run: .venv/bin/python scripts/load_check.py --seconds 20
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import httpx
import psycopg
from psycopg import sql
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash

from volleyball import create_app
from volleyball.common import now
from volleyball.db import get_db, set_scope
from volleyball.models import (Court, CourtAssignment, Division, Entry, Grant, Match,
    MatchEvent, Membership, Organization, ResultRevision, ScorerSession, Stage, Team, Token, Tournament, User, Venue)
from volleyball.services.recovery import journal_projection
from volleyball.services.rules import validate_rules


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3)


def distribution(values):
    return {"samples": len(values), "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95),
            "p99_ms": percentile(values, .99), "max_ms": round(max(values), 3) if values else None}


def local_test_urls():
    load_dotenv(ROOT / ".env")
    owner, runtime = (make_url(os.environ[name]) for name in ("TEST_DATABASE_URL", "TEST_RUNTIME_URL"))
    for url in (owner, runtime):
        if (not url.drivername.startswith("postgresql") or url.host not in {"127.0.0.1", "localhost"}
                or url.port != 55432 or not url.database or "test" not in url.database.casefold()):
            raise RuntimeError("Load validation is restricted to the generated local PostgreSQL test URLs on port 55432.")
    if (owner.host, owner.port) != (runtime.host, runtime.port):
        raise RuntimeError("Test owner/runtime URLs must target the same isolated local cluster.")
    return owner, runtime


def provision_database(owner):
    database = "volleyball_load_" + uuid4().hex[:12]
    dsn = owner.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(owner.username)))
    env = dict(os.environ)
    env["MIGRATION_DATABASE_URL"] = owner.set(database=database).render_as_string(hide_password=False)
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                            text=True, capture_output=True)
    if result.returncode:
        drop_database(owner, database)
        raise RuntimeError("Disposable load database migration failed; no existing database was changed.")
    return database


def drop_database(owner, database):
    if not database.startswith("volleyball_load_"):
        raise RuntimeError("Refusing to remove a database without the disposable load prefix.")
    dsn = owner.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


class HTTPResponse:
    """Keep the small Flask test-response interface used by the load generator."""
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code

    def get_json(self):
        return self.response.json()

    @property
    def json(self):
        return self.get_json()

    def get_data(self, as_text=False):
        return self.response.text if as_text else self.response.content


class HTTPClient:
    """Independent logical cookie session using one thread-safe connection pool."""
    def __init__(self, pool, base_url, cookie=''):
        self.pool, self.base_url, self.cookie = pool, base_url, cookie

    def request(self, method, path, json=None, headers=None):
        # An explicit Cookie header prevents the shared pool's cookie jar from
        # inheriting another logical viewer's or referee's session.
        response = self.pool.request(method, self.base_url + path, json=json,
                                     headers={'Cookie': self.cookie, **(headers or {})})
        for value in response.headers.get_list('set-cookie'):
            incoming = SimpleCookie()
            incoming.load(value)
            if 'session' in incoming:
                self.cookie = 'session=' + incoming['session'].value
        return HTTPResponse(response)

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request('PATCH', path, **kwargs)


def start_http_server(app, port, workers, threads):
    if port == 5050:
        raise RuntimeError('The existing development application port is excluded from load validation.')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', port))
    env = dict(os.environ)
    env.update(DATABASE_URL=app.config['DATABASE_URL'], SECRET_KEY=app.config['SECRET_KEY'],
               APP_ENV='test', SESSION_COOKIE_SECURE='false', EMAIL_DELIVERY_MODE='deferred')
    server = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--bind', f'127.0.0.1:{port}',
        '--workers', str(workers), '--threads', str(threads), '--worker-class', 'gthread',
        '--timeout', '60', '--access-logfile', '/dev/null', '--error-logfile', '/dev/null',
        'volleyball:create_app()'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    base_url = f'http://127.0.0.1:{port}'
    deadline = time.monotonic() + 15
    try:
        with httpx.Client(timeout=2, trust_env=False) as probe:
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError('The isolated gunicorn fixture server exited before readiness.')
                try:
                    response = probe.get(base_url + '/api/v1/health/live')
                    if response.status_code == 200:
                        return server, base_url
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
        raise RuntimeError('The isolated gunicorn fixture server did not become ready.')
    except Exception:
        stop_http_server(server)
        raise


def stop_http_server(server):
    if server.poll() is None:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait(timeout=5)


def fixture(app, game_count):
    """Prepare delegated ready sessions, then start every game through the API."""
    organizations, games = [], []
    password = secrets.token_urlsafe(24)
    with app.app_context():
        db = get_db()
        for index in range(2):
            org = Organization(name=f"LOAD VALIDATION organization {index + 1} · Demo", slug="load-org-" + uuid4().hex[:12],
                               active=True, demo=True, settings={"require_mfa": False})
            user = User(email="load-" + uuid4().hex + "@example.invalid", name=f"Load organizer {index + 1}",
                        active=True, password_hash=generate_password_hash(password, method="scrypt"))
            db.add_all([org, user])
            db.flush()
            db.add(Membership(organization_id=org.id, user_id=user.id, role="admin", status="active"))
            set_scope(org.id)
            venue = Venue(organization_id=org.id, name="Isolated load gym", timezone="America/New_York")
            db.add(venue)
            db.flush()
            tournament = Tournament(organization_id=org.id, name="LOAD VALIDATION event · Demo", slug="load-event-" + uuid4().hex[:12],
                start_date=now().date(), end_date=now().date() + timedelta(days=1), timezone="America/New_York",
                status="published", visibility="public", rules=validate_rules({}),
                settings={"demo": True, "venue_ids": [venue.id]})
            db.add(tournament)
            db.flush()
            division = Division(organization_id=org.id, tournament_id=tournament.id, name="Load division")
            db.add(division)
            db.flush()
            stage = Stage(organization_id=org.id, tournament_id=tournament.id, division_id=division.id, name="Load fixtures", format="league")
            db.add(stage)
            db.flush()
            organizations.append({"id": org.id, "slug": tournament.slug, "email": user.email, "password": password,
                                  "tournament_id": tournament.id, "venue_id": venue.id, "division_id": division.id,
                                  "stage_id": stage.id, "user_id": user.id, "matches": set()})
            db.commit()
        for index in range(game_count):
            org = organizations[index % 2]
            set_scope(org["id"])
            entries = []
            for side in ("A", "B"):
                team = Team(organization_id=org["id"], name=f"Load team {index + 1}{side}", contact={"email": "private-load-contact@example.invalid"})
                db.add(team)
                db.flush()
                entry = Entry(organization_id=org["id"], tournament_id=org["tournament_id"], division_id=org["division_id"],
                              team_id=team.id, name=team.name, seed=index * 2 + len(entries) + 1)
                db.add(entry)
                db.flush()
                entries.append(entry)
            court = Court(organization_id=org["id"], venue_id=org["venue_id"], name=f"Load court {index + 1}")
            db.add(court)
            db.flush()
            match = Match(organization_id=org["id"], tournament_id=org["tournament_id"], division_id=org["division_id"],
                stage_id=org["stage_id"], entry_a_id=entries[0].id, entry_b_id=entries[1].id,
                status="ready", generation=1, version=0, state={}, referee_name=f"Unverified load scorer {index + 1}")
            db.add(match)
            db.flush()
            db.add(CourtAssignment(organization_id=org["id"], match_id=match.id, court_id=court.id, assigned_by=org["user_id"],
                status="reserved", expires_at=now() + timedelta(minutes=30)))
            grant = Grant(organization_id=org["id"], match_id=match.id,
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(), issued_by=org["user_id"], expires_at=now() + timedelta(minutes=15))
            db.add(grant)
            db.flush()
            raw = secrets.token_urlsafe(40)
            scorer = ScorerSession(organization_id=org["id"], match_id=match.id, grant_id=grant.id,
                token_hash=hashlib.sha256(raw.encode()).hexdigest(), generation=1, name="Unverified load scorer", expires_at=now() + timedelta(hours=24))
            db.add(scorer)
            db.flush()
            csrf = secrets.token_urlsafe(32)
            client = app.test_client()
            with client.session_transaction() as cookie:
                cookie["referee_token"], cookie["referee_session_id"], cookie["csrf_token"] = raw, scorer.id, csrf
            games.append({"id": match.id, "organization_id": org["id"], "client": client, "csrf": csrf,
                          "generation": 1, "version": 0, "sequence": 0, "accepted": [], "pending": None})
            org["matches"].add(match.id)
            if "team_id" not in org:
                org["team_id"] = entries[0].team_id
            db.commit()
    for game in games:
        start = game["client"].post("/api/v1/referee/action", json={"client_action_id": str(uuid4()),
            "expected_version": game["version"], "generation": game["generation"], "type": "start", "payload": {}},
            headers={"X-CSRF-Token": game["csrf"]})
        if start.status_code != 200 or not start.json.get("saved"):
            raise RuntimeError("A delegated load fixture could not start through the scoring API.")
        game["version"] = start.json["match"]["version"]
    return organizations, games


def run_load(app, organizations, games, viewer_count, seconds, reader_workers, interval,
             client_factory=None, reader_endpoint='full'):
    lock = threading.Lock()
    acknowledgments = {}
    stats = {"write_ack": [], "read_service": [], "read_observed": [], "read_queue": [], "visibility": [],
             "write_scheduling": [], "errors": Counter(), "duplicate_retries": 0, "duplicate_retry_errors": 0,
             "read_requests_offered": 0, "score_requests_offered": 0,
             "viewer_ids_seen": set(), "scope_violations": 0, "secret_disclosures": 0,
             "initial_full_state_latency": [], "initial_full_state_errors": 0,
             "write_ticks_skipped": 0, "read_ticks_skipped": 0}
    client_factory = client_factory or app.test_client
    viewers = [{"id": i, "organization": organizations[i % 2], "client": client_factory(), "delivered": set(),
                "pending": None, "last_versions": {}, "last_response_at": 0}
               for i in range(viewer_count)]
    def initial_state(viewer):
        began = time.monotonic()
        response = viewer['client'].get('/api/v1/public/' + viewer['organization']['slug'])
        ended = time.monotonic()
        data = response.get_json() or {}
        with lock:
            stats['initial_full_state_latency'].append((ended - began) * 1000)
            if response.status_code != 200 or {match['id'] for match in data.get('matches', [])} != viewer['organization']['matches']:
                stats['initial_full_state_errors'] += 1
    # Every logical viewer loads and validates full state before live polling.
    # Initialization is recorded separately and excluded from the scoring window.
    with ThreadPoolExecutor(max_workers=reader_workers) as readers:
        list(readers.map(initial_state, viewers))
    if stats['initial_full_state_errors']:
        raise RuntimeError('A logical viewer could not initialize its permitted full public state.')
    start = time.monotonic()
    end, drain_end = start + seconds, start + seconds + max(8, interval * 3)
    for i, game in enumerate(games):
        game["next_due"] = start + i / len(games)
    for i, viewer in enumerate(viewers):
        viewer["next_due"] = start + i * interval / viewer_count

    def writer_job(game, due):
        began = time.monotonic()
        with lock:
            stats["score_requests_offered"] += 1
        action_id = str(uuid4())
        payload = {"client_action_id": action_id, "expected_version": game["version"], "generation": game["generation"],
                   "type": "point", "payload": {"team": "a" if game["sequence"] % 2 == 0 else "b"}}
        try:
            response = game["client"].post("/api/v1/referee/action", json=payload, headers={"X-CSRF-Token": game["csrf"]})
            ended = time.monotonic()
            data = response.get_json() or {}
            with lock:
                stats["write_scheduling"].append((began - due) * 1000)
                if response.status_code != 200 or not data.get("saved"):
                    stats["errors"][("score", response.status_code, data.get("error", {}).get("code", "unknown"))] += 1
                    return
                stats["write_ack"].append((ended - began) * 1000)
                version = data["match"]["version"]
                acknowledgments[(game["id"], version)] = ended
                key = (game['id'], version)
                # PostgreSQL may publish this committed version to a viewer
                # while the score response is still being serialized. Such a
                # score is already visible at acknowledgment, with zero delay.
                for viewer in viewers:
                    if (game['id'] in viewer['organization']['matches']
                            and viewer['last_versions'].get(game['id'], -1) >= version
                            and key not in viewer['delivered']):
                        viewer['delivered'].add(key)
                        stats['visibility'].append(max(0, viewer['last_response_at'] - ended) * 1000)
                game["accepted"].append(action_id)
                game["version"], game["sequence"] = version, game["sequence"] + 1
            if game["sequence"] % 5 == 0:
                retry = game["client"].post("/api/v1/referee/action", json=payload, headers={"X-CSRF-Token": game["csrf"]})
                with lock:
                    stats["duplicate_retries"] += 1
                    if retry.status_code != 200 or not retry.json.get("duplicate") or retry.json["match"]["version"] != version:
                        stats["duplicate_retry_errors"] += 1
        except Exception as exc:
            with lock:
                stats["errors"][("score", "exception", type(exc).__name__)] += 1

    def reader_job(viewer, enqueued):
        began = time.monotonic()
        with lock:
            stats["read_requests_offered"] += 1
        try:
            suffix = '/live' if reader_endpoint == 'live' else ''
            response = viewer["client"].get("/api/v1/public/" + viewer["organization"]["slug"] + suffix)
            ended = time.monotonic()
            data = response.get_json() or {}
            with lock:
                stats["read_queue"].append((began - enqueued) * 1000)
                stats["read_service"].append((ended - began) * 1000)
                stats["read_observed"].append((ended - enqueued) * 1000)
                if response.status_code != 200:
                    stats["errors"][("public", response.status_code, data.get("error", {}).get("code", "unknown"))] += 1
                    return
                stats["viewer_ids_seen"].add(viewer["id"])
                matches = {m["id"]: m["version"] for m in data.get("matches", [])}
                viewer['last_versions'], viewer['last_response_at'] = matches, ended
                if set(matches) != viewer["organization"]["matches"]:
                    stats["scope_violations"] += 1
                if any(s in response.get_data(as_text=True) for s in ("token_hash", "password_hash", "private-load-contact", "referee_token", "recovery_hashes")):
                    stats["secret_disclosures"] += 1
                for key, committed in acknowledgments.items():
                    mid, version = key
                    if mid in matches and version <= matches[mid] and key not in viewer["delivered"]:
                        viewer["delivered"].add(key)
                        stats["visibility"].append(max(0, ended - committed) * 1000)
        except Exception as exc:
            with lock:
                stats["errors"][("public", "exception", type(exc).__name__)] += 1

    with ThreadPoolExecutor(max_workers=len(games), thread_name_prefix="load-score") as writers, \
            ThreadPoolExecutor(max_workers=reader_workers, thread_name_prefix="load-public") as readers:
        while True:
            current = time.monotonic()
            for game in games:
                if game["pending"] and game["pending"].done():
                    game["pending"].result()
                    game["pending"] = None
                if current < end and current >= game["next_due"]:
                    due = game["next_due"]
                    game["next_due"] += 1
                    if game["pending"]:
                        stats["write_ticks_skipped"] += 1
                    else:
                        game["pending"] = writers.submit(writer_job, game, due)
            for viewer in viewers:
                if viewer["pending"] and viewer["pending"].done():
                    viewer["pending"].result()
                    viewer["pending"] = None
                if current >= viewer["next_due"] and current < drain_end:
                    due = viewer["next_due"]
                    viewer["next_due"] += interval
                    if viewer["pending"]:
                        stats["read_ticks_skipped"] += 1
                    else:
                        viewer["pending"] = readers.submit(reader_job, viewer, due)
            if current >= end and not any(g["pending"] for g in games):
                with lock:
                    expected = sum(1 for v in viewers for key in acknowledgments if key[0] in v["organization"]["matches"])
                    visible = sum(len(v["delivered"]) for v in viewers)
                if expected and expected == visible:
                    break
            if current >= drain_end:
                break
            time.sleep(.002)
    expected_visibility = sum(1 for v in viewers for key in acknowledgments if key[0] in v["organization"]["matches"])
    stats["expected_visibility"] = expected_visibility
    stats["observed_visibility"] = sum(len(v["delivered"]) for v in viewers)
    stats["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return stats


def inspect_correctness(app, organizations, games):
    durable, projections, active_courts, extra_events, missing_acknowledged = 0, True, 0, 0, 0
    with app.app_context():
        db = get_db()
        role = db.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        db_settings = {key: db.execute(text("SHOW " + key)).scalar() for key in
                       ("server_version", "shared_buffers", "max_connections", "synchronous_commit", "fsync")}
        for org in organizations:
            set_scope(org["id"])
            for game in (g for g in games if g["organization_id"] == org["id"]):
                rows = db.scalars(select(MatchEvent).where(MatchEvent.match_id == game["id"], MatchEvent.type == "point")).all()
                committed = {e.client_action_id for e in rows}
                acknowledged = set(game["accepted"])
                durable += len(committed)
                extra_events += len(rows) - len(committed)
                missing_acknowledged += len(acknowledged - committed)
                match = db.get(Match, game["id"])
                projections = projections and journal_projection(match)["consistent"]
                projections = projections and sum(s["a"] + s["b"] for s in match.state["sets"]) == len(committed)
                assert match.status == "in_progress"
            active_courts += db.scalar(select(func.count()).select_from(CourtAssignment).where(
                CourtAssignment.organization_id == org["id"], CourtAssignment.released_at.is_(None), CourtAssignment.status == "occupied"))
    # Exercise private authorization with a separately authenticated organizer.
    organizer = app.test_client()
    csrf = organizer.get("/api/v1/auth/me").json["csrf_token"]
    login = organizer.post("/api/v1/auth/login", json={"email": organizations[0]["email"], "password": organizations[0]["password"]},
                           headers={"X-CSRF-Token": csrf})
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json["csrf_token"], "X-Organization-ID": organizations[0]["id"]}
    foreign = organizer.patch("/api/v1/teams/" + organizations[1]["team_id"], json={"name": "Forbidden cross-scope edit"}, headers=headers)
    headers["X-Organization-ID"] = organizations[1]["id"]
    foreign_membership = organizer.get("/api/v1/teams", headers=headers)
    return {"durable_point_events": durable, "duplicate_committed_actions": extra_events,
            "lost_acknowledged_actions": missing_acknowledged, "journal_and_score_projections_consistent": bool(projections),
            "occupied_courts": active_courts, "runtime_role_superuser": role[0], "runtime_role_bypassrls": role[1],
            "cross_organization_object_denied": foreign.status_code == 404,
            "cross_organization_membership_denied": foreign_membership.status_code == 403,
            "database_settings": db_settings}


def save_query_plans(app, organizations, games, output):
    """Add isolated report history after measurement, then EXPLAIN as runtime."""
    historical = {}
    with app.app_context():
        db = get_db()
        for org in organizations:
            set_scope(org['id'])
            tournament = Tournament(organization_id=org['id'], name='QUERY PLAN FIXTURE · Demo history',
                slug='query-history-' + uuid4().hex[:12], start_date=now().date() - timedelta(days=90),
                end_date=now().date() - timedelta(days=1), status='archived', visibility='private',
                timezone='America/New_York', rules=validate_rules({}), settings={'demo': True})
            db.add(tournament)
            db.flush()
            division = Division(organization_id=org['id'], tournament_id=tournament.id, name='Isolated report history')
            db.add(division)
            db.flush()
            stage = Stage(organization_id=org['id'], tournament_id=tournament.id, division_id=division.id,
                          name='Archived fixture season', format='league')
            db.add(stage)
            db.flush()
            originals = db.scalars(select(Entry).where(Entry.tournament_id == org['tournament_id']).order_by(Entry.seed).limit(2)).all()
            entries = [Entry(organization_id=org['id'], tournament_id=tournament.id, division_id=division.id,
                team_id=entry.team_id, name=entry.name, seed=index + 1) for index, entry in enumerate(originals)]
            db.add_all(entries)
            db.flush()
            state = {'sets': [{'a': 25, 'b': 20, 'complete': True, 'winner': 'a'} for _ in range(2)],
                'set_index': 1, 'serve': 'a', 'timeouts': {'a': 2, 'b': 2}, 'clock_seconds': 1800, 'clock_anchor': None}
            matches = [Match(organization_id=org['id'], tournament_id=tournament.id, division_id=division.id,
                stage_id=stage.id, entry_a_id=entries[0].id, entry_b_id=entries[1].id, status='ended',
                result_status='finalized', winner_id=entries[0].id, result_revision=1, version=1,
                actual_start=now() - timedelta(days=1, hours=index + 1), actual_end=now() - timedelta(days=1, hours=index),
                state=state, rule_snapshot=validate_rules({}), settings={'demo': True, 'query_plan_fixture': True}) for index in range(500)]
            db.add_all(matches)
            db.flush()
            db.add_all([ResultRevision(organization_id=org['id'], match_id=match.id, revision=1, sets=state['sets'],
                winner_id=match.winner_id, status='finalized', approved_at=match.actual_end,
                reason='Explicit isolated query-plan fixture; no invented rally history') for match in matches])
            db.commit()
            historical[org['id']] = tournament.id
        org = organizations[0]
        game = next(game for game in games if game['organization_id'] == org['id'])
        set_scope(org['id'])
        role = db.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        assert role == (False, False)
        revision = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
        shared = {'org': org['id']}
        plans = [
            ('Tenant match projection',
             'SELECT id,version,status,state FROM matches WHERE organization_id=:org AND tournament_id=:event ORDER BY planned_at,round_number LIMIT 1000',
             {**shared, 'event': org['tournament_id']}),
            ('Idempotency receipt by match and action UUID',
             'SELECT sequence,new_version,receipt FROM match_events WHERE match_id=:match AND client_action_id=:action LIMIT 1',
             {'match': game['id'], 'action': game['accepted'][0] if game['accepted'] else str(uuid4())}),
            ('Scoped finalized results and latest immutable revision',
             "SELECT m.id,m.actual_end,m.winner_id,r.revision,r.sets FROM matches m JOIN result_revisions r ON r.match_id=m.id AND r.organization_id=m.organization_id AND r.revision=m.result_revision WHERE m.organization_id=:org AND m.tournament_id=:event AND m.result_status='finalized' ORDER BY m.actual_end DESC LIMIT 100",
             {**shared, 'event': historical[org['id']]}),
        ]
        throttle_key = db.scalar(select(Token.data).where(Token.kind == 'public_limit').limit(1))
        if throttle_key:
            plans.append(('Persisted public rate-limit lookup',
                "SELECT count(*) FROM identity_tokens WHERE kind='public_limit' AND data ? 'throttle_key' AND data->>'throttle_key'=:key AND expires_at>now()",
                {'key': throttle_key['throttle_key']}))
        sections = ['# Measured local PostgreSQL query plans', '',
            'Collected after the isolated load window using the nonsuperuser runtime role with RLS enabled. '
            'The transaction organization scope is set through the application helper. IDs and throttle hashes are replaced with parameter labels.', '',
            f'Measured at: {now().isoformat()}. Schema revision: `{revision}`. ', '',
            'Fixture: two isolated organizations, 25 live games, accepted scoring journals from the measured baseline, '
            'and 1,000 explicitly simulated finalized historical matches plus result revisions. '
            'Historical rows are added after the load measurement only to exercise reports; they do not change the public workload. '
            'These small local tables and warm buffers do not establish production query costs.', '']
        for title, query, params in plans:
            rows = db.execute(text('EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT TEXT) ' + query), params).scalars().all()
            measured = '\n'.join(rows)
            for key, value in params.items():
                measured = measured.replace(str(value), '<' + key.upper() + '>')
            measured = measured.replace(org['id'], '<ORG>')
            sections += ['## ' + title, '', '```sql', query, '```', '', '```text', measured, '```', '']
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('\n'.join(sections))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--games", type=int, default=25)
    parser.add_argument("--viewers", type=int, default=500)
    parser.add_argument("--reader-workers", type=int, default=128)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--keep-database", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "load-validation.json")
    parser.add_argument("--query-plans", type=Path, default=ROOT / "docs" / "query-plans.md")
    parser.add_argument('--transport', choices=('wsgi', 'gunicorn'), default='gunicorn')
    parser.add_argument('--reader-endpoint', choices=('full', 'live'), default='live')
    parser.add_argument('--server-workers', type=int, default=2)
    parser.add_argument('--server-threads', type=int, default=8)
    parser.add_argument('--port', type=int, default=5059)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120 or not 2 <= args.games <= 100 or not 2 <= args.viewers <= 2000 or not 4 <= args.reader_workers <= 128 or not .5 <= args.poll_seconds <= 10:
        raise SystemExit("Use a bounded local window: seconds 5–120, games 2–100, viewers 2–2000, reader-workers 4–128, poll-seconds 0.5–10.")
    if not 1 <= args.server_workers <= 4 or not 1 <= args.server_threads <= 16 or not 1024 <= args.port <= 65535 or args.port in (5050, 55432):
        raise SystemExit('Use 1–4 fixture workers, 1–16 threads, and an unused loopback port excluding the development application/database.')
    owner, runtime = local_test_urls()
    database, app, server, http_pool = provision_database(owner), None, None, None
    try:
        app = create_app({"TESTING": True, "APP_ENV": "test", "DATABASE_URL": runtime.set(database=database).render_as_string(hide_password=False),
            "SECRET_KEY": secrets.token_hex(48), "SESSION_COOKIE_SECURE": False, "EMAIL_DELIVERY_MODE": "deferred"})
        organizations, games = fixture(app, args.games)
        client_factory = None
        if args.transport == 'gunicorn':
            server, base_url = start_http_server(app, args.port, args.server_workers, args.server_threads)
            http_pool = httpx.Client(timeout=httpx.Timeout(30, connect=5, pool=10), trust_env=False,
                limits=httpx.Limits(max_connections=args.games + args.reader_workers + 4,
                                   max_keepalive_connections=args.games + args.reader_workers + 4))
            for game in games:
                cookie = game['client'].get_cookie('session')
                game['client'] = HTTPClient(http_pool, base_url, 'session=' + cookie.value)
            client_factory = lambda: HTTPClient(http_pool, base_url)
        stats = run_load(app, organizations, games, args.viewers, args.seconds, args.reader_workers, args.poll_seconds,
                         client_factory=client_factory, reader_endpoint=args.reader_endpoint)
        if server:
            stop_http_server(server)
        if http_pool:
            http_pool.close()
        correctness = inspect_correctness(app, organizations, games)
        save_query_plans(app, organizations, games, args.query_plans)
        errors = sum(stats["errors"].values())
        total = stats["score_requests_offered"] + stats["read_requests_offered"]
        correct = (not correctness["duplicate_committed_actions"] and not correctness["lost_acknowledged_actions"]
            and correctness["journal_and_score_projections_consistent"] and correctness["occupied_courts"] == args.games
            and correctness["cross_organization_object_denied"] and correctness["cross_organization_membership_denied"]
            and not stats["scope_violations"] and not stats["secret_disclosures"] and not stats["duplicate_retry_errors"])
        write_p95, visibility_p95 = percentile(stats["write_ack"], .95), percentile(stats["visibility"], .95)
        report = {
            "validated_at": now().isoformat(), "status": "measured", "correctness_passed": bool(correct),
            "environment": {"os": platform.platform(), "architecture": platform.machine(), "logical_cpus": os.cpu_count(),
                "python": platform.python_version(), "transport": ('HTTP/1.1 over loopback TCP to isolated gunicorn; PostgreSQL TCP on local127.0.0.1:55432' if args.transport == 'gunicorn' else 'Flask in-process WSGI test clients; PostgreSQL TCP on local127.0.0.1:55432'),
                "application_processes": args.server_workers if args.transport == 'gunicorn' else 1,
                "application_threads_per_process": args.server_threads if args.transport == 'gunicorn' else args.games + args.reader_workers,
                "load_generator_processes": 1, "generator_shares_application_process": args.transport == 'wsgi',
                "generator_writer_threads": args.games, "generator_reader_threads": args.reader_workers,
                "database_pool_max_connections_per_application_process": 30, "database": database, "database_disposable": True,
                "database_preserved": args.keep_database, "database_settings": correctness.pop("database_settings")},
            "workload": {"organizations": 2, "active_scoring_games": args.games, "logical_concurrent_viewers": args.viewers,
                "distinct_viewers_served": len(stats["viewer_ids_seen"]), "score_actions_per_game_per_second": 1,
                "viewer_poll_seconds": args.poll_seconds, "offered_read_requests_per_second": args.viewers / args.poll_seconds,
                "measurement_window_seconds": args.seconds, "total_with_delivery_drain_seconds": stats["elapsed_seconds"],
                "read_requests": stats["read_requests_offered"], "score_requests": stats["score_requests_offered"],
                "acknowledged_score_actions": len(stats["write_ack"]),
                "writer_ticks_skipped_due_to_pending_request": stats["write_ticks_skipped"],
                "viewer_ticks_skipped_due_to_pending_request": stats["read_ticks_skipped"],
                "initial_full_state_reads": len(stats['initial_full_state_latency']),
                "reader_endpoint": '/api/v1/public/<slug>/live' if args.reader_endpoint == 'live' else '/api/v1/public/<slug>',
                "initial_full_state_endpoint": '/api/v1/public/<slug>'},
            'initial_full_public_state_http_latency': distribution(stats['initial_full_state_latency']),
            "durable_score_ack": distribution(stats["write_ack"]),
            "public_read_server_and_client_ms": distribution(stats["read_service"]),
            "public_read_including_generator_queue_ms": distribution(stats["read_observed"]),
            "load_generator_reader_queue_ms": distribution(stats["read_queue"]),
            "load_generator_writer_scheduling_ms": distribution(stats["write_scheduling"]),
            "committed_score_public_visibility": {**distribution(stats["visibility"]),
                "expected_viewer_score_observations": stats["expected_visibility"], "observed": stats["observed_visibility"],
                "not_observed_before_drain_ended": stats["expected_visibility"] - stats["observed_visibility"],
                "fraction_of_expected_observations_within_2_seconds": round(sum(delay <= 2000 for delay in stats["visibility"]) / stats["expected_visibility"], 6) if stats["expected_visibility"] else None,
                "definition": "From Saved response acknowledgment until a periodic public response contains that committed match version or newer."},
            'measurement_notes': ['A viewer that has already received the committed version when Saved is acknowledged has zero visibility delay. Initial full-state requests are recorded separately from the live scoring window.'],
            "errors": {"count": errors, "request_error_rate": round(errors / total, 6) if total else None,
                "samples": [{"operation": k[0], "status": k[1], "code": k[2], "count": v} for k, v in stats["errors"].items()]},
            "correctness": {**correctness, "duplicate_retry_requests": stats["duplicate_retries"],
                "duplicate_retry_errors": stats["duplicate_retry_errors"], "public_scope_violations": stats["scope_violations"], "public_secret_disclosures": stats["secret_disclosures"]},
            "targets": {"durable_ack_p95_ms": 1000, "durable_ack_target_met": write_p95 is not None and write_p95 <= 1000,
                "committed_live_delivery_p95_ms": 2000, "live_delivery_target_met": visibility_p95 is not None and visibility_p95 <= 2000,
                'all_observed_scores_delivered_within_2_seconds': bool(stats['visibility']) and max(stats['visibility']) <= 2000 and stats['observed_visibility'] == stats['expected_visibility'],
                "baseline_games_and_viewers_met": args.games >= 25 and len(stats["viewer_ids_seen"]) >= 500,
                "mobile_lcp_and_inp_measured": False},
            "limits": [("Loopback HTTP includes gunicorn/socket overhead but excludes TLS, reverse proxies and external networks; this does not establish production capacity." if args.transport == 'gunicorn' else 'In-process WSGI excludes gunicorn/socket/TLS/proxy overhead and does not establish production capacity.'),
                "Local PostgreSQL and application share the same machine; no mobile radio, packet loss or geographic latency is simulated.",
                "Bounded generator threads and one outstanding request per logical viewer apply client backpressure; queue delay and missed ticks are reported.",
                "This is a short baseline, not a soak test; browser LCP/INP, real-device behavior and licensed integrations are unmeasured."]}
        before_path = ROOT / 'docs' / 'load-validation-before-optimization.json'
        if before_path.exists():
            before = json.loads(before_path.read_text())
            report['comparison'] = {'previous_report': str(before_path.relative_to(ROOT)),
                'previous_validated_at': before['validated_at'],
                'previous_score_ack_p95_ms': before['durable_score_ack']['p95_ms'],
                'previous_public_read_p95_ms': before['public_read_server_and_client_ms']['p95_ms'],
                'previous_public_visibility_p95_ms': before['committed_score_public_visibility']['p95_ms']}
        serialized_path = ROOT / 'docs' / 'load-validation-after-serialization.json'
        if serialized_path.exists():
            serialized = json.loads(serialized_path.read_text())
            report.setdefault('comparison', {})['serialization_only_report'] = str(serialized_path.relative_to(ROOT))
            report['comparison']['serialization_only_score_ack_p95_ms'] = serialized['durable_score_ack']['p95_ms']
            report['comparison']['serialization_only_public_visibility_p95_ms'] = serialized['committed_score_public_visibility']['p95_ms']
        two_worker_path = ROOT / 'docs' / 'load-validation-http-two-workers.json'
        if two_worker_path.exists() and args.transport == 'gunicorn' and args.server_workers != 2:
            two_worker = json.loads(two_worker_path.read_text())
            report.setdefault('comparison', {})['http_two_worker_report'] = str(two_worker_path.relative_to(ROOT))
            report['comparison']['http_two_worker_score_ack_p95_ms'] = two_worker['durable_score_ack']['p95_ms']
            report['comparison']['http_two_worker_public_visibility_p95_ms'] = two_worker['committed_score_public_visibility']['p95_ms']
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"report": str(args.output), "correctness_passed": correct, "score_ack_p95_ms": write_p95,
                          "public_visibility_p95_ms": visibility_p95, "errors": errors}))
        return 0 if correct else 1
    finally:
        if server:
            stop_http_server(server)
        if http_pool:
            http_pool.close()
        if app:
            app.extensions["db_engine"].dispose()
        if not args.keep_database:
            drop_database(owner, database)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Database tracebacks can embed statement parameters. Fixture secrets
        # and connection credentials must never appear in command output.
        print(json.dumps({'load_failed': type(exc).__name__,
                          'message': str(exc) if isinstance(exc, RuntimeError) else 'Isolated load validation failed; inspect the local code/configuration.'}))
        raise SystemExit(1)
