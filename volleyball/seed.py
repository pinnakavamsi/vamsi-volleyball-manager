"""Explicit, isolated development fixtures. No credentials or production assumptions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, g
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from .auth import normalize_email, validate_password
from .common import APIError, audit, now, uuid_str
from .db import set_scope
from .models import (Announcement, Court, CourtAssignment, Division, Entry, Match,
    MatchEvent, Membership, Organization, ResultRevision, RuleVersion, Stage, Team, Tournament,
    TournamentVenue, User, Venue)
from .services.formats import bracket, round_robin
from .services.rules import DEFAULT_RULES


def seed_demo(db, email, password):
    """Create only named demo organizations in a separately identified database."""
    email, password = normalize_email(email), validate_password(password)
    database_name = db.bind.url.database or ""
    if "demo" not in database_name.casefold() and not current_app.config.get("ALLOW_DEMO_SEED", False):
        raise APIError("Demo seeding requires a database with 'demo' in its name or explicit ALLOW_DEMO_SEED=true.")
    if db.scalar(select(Organization).where(Organization.demo.is_(False))):
        raise APIError("Demo data cannot be added to a database containing a production organization.", 409)
    if db.scalar(select(Organization).where(Organization.slug == "hudson-demo")):
        raise APIError("Demo fixtures already exist. Keep the existing data or recreate the isolated demo database.", 409)
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email, name="Demo organizer", active=True,
                    password_hash=generate_password_hash(password, method="scrypt"))
        db.add(user)
        db.flush()
    local, domain = email.rsplit("@", 1)
    secondary_admin_email = normalize_email(local + "+harbor-admin@" + domain)
    secondary_admin = db.scalar(select(User).where(User.email == secondary_admin_email))
    if not secondary_admin:
        secondary_admin = User(email=secondary_admin_email, name="Harbor demo administrator", active=True,
            password_hash=generate_password_hash(password, method="scrypt"))
        db.add(secondary_admin)
        db.flush()
    g.user = user
    timezone = "America/New_York"
    local_today = now().astimezone(ZoneInfo(timezone)).date()
    primary = Organization(name="Hudson Volleyball Club · Demo", slug="hudson-demo", timezone=timezone,
        demo=True, color="#0d9488", settings={"require_mfa": False, "auto_finalize": False, "qr_admission_minutes": 15})
    secondary = Organization(name="Harbor Volleyball League · Demo", slug="harbor-demo", timezone=timezone,
        demo=True, color="#7c3aed", settings={"require_mfa": False, "auto_finalize": False, "qr_admission_minutes": 15})
    db.add_all([primary, secondary])
    db.flush()
    db.add_all([Membership(user_id=user.id, organization_id=primary.id, role="admin", status="active", activated_at=now()),
                Membership(user_id=user.id, organization_id=secondary.id, role="manager", status="active", activated_at=now()),
                Membership(user_id=secondary_admin.id, organization_id=secondary.id, role="admin", status="active", activated_at=now())])
    db.flush()
    set_scope(primary.id)
    g.organization = primary
    venue = Venue(organization_id=primary.id, name="Riverside Sports Center · Demo", address="Demo venue, New York, NY",
        latitude=40.758, longitude=-73.9855, timezone=timezone, indoor=True,
        settings={"opening_hours": {str(day): {"open": "06:00", "close": "23:00"} for day in range(7)}})
    db.add(venue)
    db.flush()
    courts = [Court(organization_id=primary.id, venue_id=venue.id, name=name, available=available)
              for name, available in (("Court 1", True), ("Court 2", True), ("Court 3", True), ("Court 4", False))]
    db.add_all(courts)
    db.flush()
    rules = deepcopy(DEFAULT_RULES)
    event = Tournament(organization_id=primary.id, name="Hudson Invitational · Demo", slug="hudson-invitational-demo",
        description="Explicitly simulated fixtures for reviewing V1. No real tournament or verified athlete data.",
        competition_type="tournament", status="active", visibility="public", start_date=local_today,
        end_date=local_today + timedelta(days=2), timezone=timezone, rules=rules,
        settings={"demo": True, "venue_ids": [venue.id], "default_match_duration": 60, "buffer_minutes": 10,
                  "rest_minutes": 30, "rules_reviewed": True, "public_reports": True},
        display={"widgets": ["live", "standings", "upcoming", "announcements", "weather"], "rotation_seconds": 15,
                 "text_size": "large", "theme": "dark"}, standings_generation=1)
    db.add(event)
    db.flush()
    db.add(TournamentVenue(organization_id=primary.id, tournament_id=event.id, venue_id=venue.id, active=True))
    _initial_rules(db, event, user)
    palettes = ["#0d9488", "#2563eb", "#9333ea", "#e11d48", "#d97706", "#475569"]
    division_specs = [
        ("Open competitive", ["River Hawks", "Metro Spikers", "Uptown Aces", "Eastside Volley", "Skyline Six", "Harbor Storm"], {}),
        ("Recreational", ["Weekend Waves", "Block Party", "Sideout Crew", "Net Results"],
         {"regular_target": 21, "deciding_target": 11, "cap": 25}),
    ]
    live_match = None
    all_matches = []
    for division_index, (division_name, names, division_rules) in enumerate(division_specs):
        division = Division(organization_id=primary.id, tournament_id=event.id, name=division_name,
            category="Open", rules=division_rules, settings={"capacity": len(names), "demo": True})
        db.add(division)
        db.flush()
        _initial_rules(db, division, user)
        pool_stage = Stage(organization_id=primary.id, tournament_id=event.id, division_id=division.id,
            name="Pool play", format="pools_knockout", position=1,
            settings={"pool_count": 2, "qualify_per_pool": 2, "repeat": 1})
        db.add(pool_stage)
        db.flush()
        _initial_rules(db, pool_stage, user)
        knockout = Stage(organization_id=primary.id, tournament_id=event.id, division_id=division.id,
            name="Championship bracket", format="single_elimination", position=2,
            settings={"source_stage_id": pool_stage.id, "qualify_per_pool": 2})
        db.add(knockout)
        db.flush()
        _initial_rules(db, knockout, user)
        entries = []
        for index, name in enumerate(names):
            team = Team(organization_id=primary.id, name=name, abbreviation="".join(n[0] for n in name.split())[:4],
                color=palettes[index % len(palettes)], contact={"name": "Demo contact", "email": "example@example.invalid"})
            db.add(team)
            db.flush()
            entry = Entry(organization_id=primary.id, tournament_id=event.id, division_id=division.id,
                team_id=team.id, name=team.name, color=team.color, seed=index + 1, pool="A" if index % 2 == 0 else "B")
            db.add(entry)
            db.flush()
            entries.append(entry)
        game_index = 0
        for pool in ("A", "B"):
            for fixture in round_robin([e.id for e in entries if e.pool == pool]):
                planned = datetime.combine(local_today, datetime.min.time(), ZoneInfo(timezone)) + timedelta(hours=9 + division_index * 4, minutes=game_index * 70)
                match = Match(organization_id=primary.id, tournament_id=event.id, division_id=division.id,
                    stage_id=pool_stage.id, entry_a_id=fixture["a"], entry_b_id=fixture["b"], pool=pool,
                    round_number=fixture["round"], planned_at=planned, planned_duration=60,
                    referee_name="Demo referee", settings={"demo": True})
                db.add(match)
                db.flush()
                effective = {**rules, **division_rules}
                if division_index == 0 and game_index == 0:
                    _result(db, match, effective, submitted=False)
                elif division_index == 0 and game_index == 3:
                    _result(db, match, effective, submitted=True)
                elif division_index == 0 and game_index == 1:
                    _live(db, match, effective, courts[0], user)
                    live_match = match
                elif division_index == 0 and game_index == 2:
                    match.status = "ready"
                    db.add(CourtAssignment(organization_id=primary.id, match_id=match.id, court_id=courts[1].id,
                        assigned_by=user.id, status="reserved", expires_at=now() + timedelta(minutes=15)))
                elif division_index == 1 and game_index == 0:
                    _result(db, match, effective, submitted=False)
                all_matches.append(match)
                game_index += 1
        # Seed qualification placeholders; the actual ranking service determines entrants.
        semifinals = []
        for index in range(2):
            semi = Match(organization_id=primary.id, tournament_id=event.id, division_id=division.id,
                stage_id=knockout.id, entry_a_id=None, entry_b_id=None, round_number=1,
                planned_at=datetime.combine(local_today + timedelta(days=1), datetime.min.time(), ZoneInfo(timezone)) + timedelta(hours=10 + index * 2),
                settings={"demo": True, "qualifier_a": f"qualifier:{index + 1}:A",
                          "qualifier_b": f"qualifier:{2 - index}:B"})
            db.add(semi)
            db.flush()
            semifinals.append(semi)
        final = Match(organization_id=primary.id, tournament_id=event.id, division_id=division.id, stage_id=knockout.id,
            source_a_id=semifinals[0].id, source_b_id=semifinals[1].id, round_number=2,
            planned_at=datetime.combine(local_today + timedelta(days=2), datetime.min.time(), ZoneInfo(timezone)) + timedelta(hours=12),
            settings={"demo": True})
        db.add(final)
    db.add(Announcement(organization_id=primary.id, tournament_id=event.id,
        title="Welcome to the demo", content="All teams, fixtures and results here are simulated. This isolated database is for product review.",
        visibility="public", publish_at=now()))
    audit("demo.seeded", "tournament", event.id, reason="Explicit development seed; all match states are simulated")
    db.flush()

    set_scope(secondary.id)
    g.organization = secondary
    second_venue = Venue(organization_id=secondary.id, name="Harbor Community Gym · Demo", address="Demo venue, Jersey City, NJ",
        latitude=40.7178, longitude=-74.0431, timezone=timezone, indoor=True)
    db.add(second_venue)
    db.flush()
    second_courts = [Court(organization_id=secondary.id, venue_id=second_venue.id, name=f"Harbor court {i}") for i in (1, 2)]
    db.add_all(second_courts)
    first_fixture_day = local_today + timedelta(days=7)
    league = Tournament(organization_id=secondary.id, name="Harbor Autumn League · Demo", slug="harbor-autumn-league-demo",
        description="Nine fortnightly league rounds over four months. Simulated data in a separate demo organization.",
        competition_type="league", status="published", visibility="private", start_date=first_fixture_day,
        end_date=first_fixture_day + timedelta(weeks=16), timezone=timezone, rules=deepcopy(DEFAULT_RULES),
        settings={"demo": True, "venue_ids": [second_venue.id], "round_interval_days": 14, "rules_reviewed": True},
        display={"widgets": ["standings", "upcoming"]})
    db.add(league)
    db.flush()
    db.add(TournamentVenue(organization_id=secondary.id, tournament_id=league.id, venue_id=second_venue.id, active=True))
    _initial_rules(db, league, user)
    league_division = Division(organization_id=secondary.id, tournament_id=league.id, name="Community season", rules={}, settings={"demo": True})
    db.add(league_division)
    db.flush()
    _initial_rules(db, league_division, user)
    league_stage = Stage(organization_id=secondary.id, tournament_id=league.id, division_id=league_division.id,
        name="Regular season", format="league", rules={"regular_target": 21, "deciding_target": 15},
        settings={"repeat": 3, "round_interval_days": 14})
    db.add(league_stage)
    db.flush()
    _initial_rules(db, league_stage, user)
    league_entries = []
    for index, name in enumerate(("Harbor Lights", "Dockside Aces", "Pier Spikers", "Bay Blockers")):
        team = Team(organization_id=secondary.id, name=name, color=palettes[index])
        db.add(team)
        db.flush()
        entry = Entry(organization_id=secondary.id, tournament_id=league.id, division_id=league_division.id,
                      team_id=team.id, name=name, color=team.color, seed=index + 1)
        db.add(entry)
        db.flush()
        league_entries.append(entry)
    for index, fixture in enumerate(round_robin([e.id for e in league_entries], repeat=3)):
        scheduled_day = first_fixture_day + timedelta(days=14 * (fixture["round"] - 1))
        planned = datetime.combine(scheduled_day, datetime.min.time(), ZoneInfo(timezone)) + timedelta(hours=18 + index % 2)
        match = Match(organization_id=secondary.id, tournament_id=league.id, division_id=league_division.id,
            stage_id=league_stage.id, entry_a_id=fixture["a"], entry_b_id=fixture["b"], round_number=fixture["round"],
            planned_at=planned, settings={"demo": True}, referee_name="League demo official")
        db.add(match)
    audit("demo.seeded", "tournament", league.id, reason="Explicit development league seed")
    db.commit()
    return {"user_id": user.id, "email": user.email, "secondary_admin_email": secondary_admin_email,
            "harbor_admin_email": secondary_admin_email,
            "organizations": [{"id": primary.id, "name": primary.name, "role": "admin"},
                              {"id": secondary.id, "name": secondary.name, "role": "manager"}],
            "tournament_slug": event.slug, "league_slug": league.slug,
            "live_match_id": live_match.id if live_match else None, "demo": True}


def _initial_rules(db, obj, user):
    scope_type = type(obj).__name__.lower()
    db.add(RuleVersion(organization_id=obj.organization_id,
        tournament_id=obj.id if isinstance(obj, Tournament) else obj.tournament_id,
        division_id=obj.id if isinstance(obj, Division) else getattr(obj, "division_id", None),
        stage_id=obj.id if isinstance(obj, Stage) else None, scope_type=scope_type, scope_id=obj.id,
        version=1, rules=deepcopy(obj.rules or {}), reason="Explicit initial demo policy", created_by=user.id))


def _result(db, match, rules, submitted=False):
    target = rules["regular_target"]
    sets = [{"a": target, "b": target - 4, "complete": True, "winner": "a"},
            {"a": target, "b": target - 6, "complete": True, "winner": "a"}]
    match.status, match.result_status, match.outcome = "ended", "submitted" if submitted else "finalized", "normal"
    match.winner_id, match.version, match.result_revision = match.entry_a_id, 1, 1
    match.rule_snapshot = deepcopy(rules)
    match.actual_start, match.actual_end = now() - timedelta(hours=2), now() - timedelta(hours=1)
    match.state = {"sets": sets, "set_index": 1, "serve": "a", "timeouts": {"a": 2, "b": 2},
                   "clock_seconds": 3600, "clock_anchor": None}
    db.add(ResultRevision(organization_id=match.organization_id, match_id=match.id, revision=1,
        sets=deepcopy(sets), winner_id=match.winner_id, outcome="normal", status=match.result_status,
        approved_by=None if submitted else g.user.id, approved_at=None if submitted else now(),
        reason="Explicitly simulated demo result; no fabricated rally history"))
    db.add(MatchEvent(organization_id=match.organization_id, match_id=match.id, sequence=1,
        client_action_id=uuid_str(), type="demo_snapshot", actor_id=g.user.id, payload={"state": deepcopy(match.state), "demo": True},
        expected_version=0, new_version=1, receipt={"version": 1, "demo": True}))
    audit("demo.result_seeded", "match", match.id, after={"result_status": match.result_status},
          reason="Explicitly simulated demo result; no fabricated rally history")


def _live(db, match, rules, court, user):
    match.status, match.version, match.generation = "in_progress", 1, 0
    match.rule_snapshot = deepcopy(rules)
    match.actual_start = now() - timedelta(minutes=7)
    match.state = {"sets": [{"a": 8, "b": 6, "complete": False, "winner": None}], "set_index": 0,
        "serve": "a", "timeouts": {"a": rules["timeouts"], "b": rules["timeouts"]},
        "clock_seconds": 420, "clock_anchor": now().isoformat()}
    db.add(CourtAssignment(organization_id=match.organization_id, match_id=match.id, court_id=court.id,
        assigned_by=user.id, status="occupied", started_at=match.actual_start, expires_at=None))
    db.add(MatchEvent(organization_id=match.organization_id, match_id=match.id, sequence=1,
        client_action_id=uuid_str(), type="demo_snapshot", actor_id=user.id, payload={"state": deepcopy(match.state), "demo": True},
        expected_version=0, new_version=1, receipt={"version": 1, "demo": True}))
    audit("demo.live_seeded", "match", match.id, reason="Explicitly simulated in-progress demo state")
