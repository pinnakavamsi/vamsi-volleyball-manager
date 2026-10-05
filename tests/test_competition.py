"""Competition format, ranking policy, qualification and correction acceptance checks."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from volleyball.common import APIError, now
from volleyball.db import get_db, set_scope
from volleyball.models import Court, Division, Entry, Match, ResultRevision, RuleVersion, Stage, Team, Tournament, Venue
from volleyball.services.formats import bracket, round_robin
from volleyball.services.rankings import standings
from volleyball.services.rules import DEFAULT_RULES, effective_rules, match_winner, set_winner, validate_rules


@pytest.fixture
def competition_case(app, admin_client, demo):
    with app.app_context():
        db = get_db()
        oid = demo["organizations"][0]["id"]
        set_scope(oid)
        event = Tournament(organization_id=oid, name="Competition acceptance cup", slug="acceptance-" + uuid4().hex[:12],
            start_date=now().date(), end_date=now().date() + timedelta(days=2), status="published", visibility="private",
            timezone="America/New_York", rules=validate_rules({"regular_target": 2, "deciding_target": 2}))
        db.add(event)
        db.flush()
        division = Division(organization_id=oid, tournament_id=event.id, name="Open", rules={})
        db.add(division)
        db.flush()
        stage = Stage(organization_id=oid, tournament_id=event.id, division_id=division.id, name="Initial stage", format="round_robin", rules={})
        db.add(stage)
        db.flush()
        venue = Venue(organization_id=oid, name="Policy test venue", timezone="America/New_York")
        db.add(venue)
        db.flush()
        court = Court(organization_id=oid, venue_id=venue.id, name="Policy test court")
        db.add(court)
        db.flush()
        entries = []
        for index in range(6):
            team = Team(organization_id=oid, name=f"Acceptance team {index + 1}")
            db.add(team)
            db.flush()
            entry = Entry(organization_id=oid, tournament_id=event.id, division_id=division.id, team_id=team.id,
                name=team.name, seed=index + 1, pool="A" if index % 2 == 0 else "B")
            db.add(entry)
            db.flush()
            entries.append(entry.id)
        db.commit()
        return {"org_id": oid, "event": event.id, "division": division.id, "stage": stage.id, "entries": entries, "court": court.id}


def configure_stage(app, case, fmt, entry_count=6, rules=None):
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        stage = db.get(Stage, case["stage"])
        stage.format = fmt
        if rules is not None:
            stage.rules = rules
        for entry_id in case["entries"][entry_count:]:
            db.get(Entry, entry_id).status = "withdrawn"
        db.commit()


def finalize_snapshot(db, case, stage_id, a, b, winner="a", sets=None, pool=None, result_status="finalized"):
    """Administrative source result fixtures, explicitly without synthetic rally actions."""
    sets = sets or [{"a": 2 if winner == "a" else 0, "b": 0 if winner == "a" else 2,
                     "complete": True, "winner": winner} for _ in range(2)]
    match = Match(organization_id=case["org_id"], tournament_id=case["event"], division_id=case["division"],
        stage_id=stage_id, entry_a_id=a, entry_b_id=b, status="ended", result_status=result_status, outcome="normal",
        winner_id=a if winner == "a" else b, version=1, result_revision=1, pool=pool,
        rule_snapshot=validate_rules({"regular_target": 2, "deciding_target": 2}),
        state={"sets": deepcopy(sets), "set_index": 1, "serve": "a", "timeouts": {"a": 2, "b": 2}, "clock_seconds": 120, "clock_anchor": None})
    db.add(match)
    db.flush()
    db.add(ResultRevision(organization_id=case["org_id"], match_id=match.id, revision=1,
        sets=deepcopy(sets), winner_id=match.winner_id, status=result_status,
        approved_at=now() if result_status == "finalized" else None,
        reason="Explicit administrative test snapshot"))
    return match


def test_a08_round_robin_preview_commit_and_repeated_encounters(app, admin_client, competition_case):
    case = competition_case
    ids = case["entries"][:4]
    fixtures = round_robin(ids, repeat=2)
    assert len(fixtures) == 12
    pairs = [frozenset((f["a"], f["b"])) for f in fixtures]
    assert len(set(pairs)) == 6 and all(pairs.count(p) == 2 for p in set(pairs))
    configure_stage(app, case, "round_robin", entry_count=4)
    preview = admin_client.post(f"/api/v1/stages/{case['stage']}/generate", json={"preview": True, "repeat": 2}, headers=admin_client.api_headers)
    assert preview.status_code == 200 and preview.get_json()["total"] == 12
    with app.app_context():
        set_scope(case["org_id"])
        assert not get_db().scalar(select(Match).where(Match.stage_id == case["stage"]))
    generated = admin_client.post(f"/api/v1/stages/{case['stage']}/generate", json={"preview": False, "repeat": 2}, headers=admin_client.api_headers)
    assert generated.status_code == 201 and generated.get_json()["created"] == 12
    assert admin_client.post(f"/api/v1/stages/{case['stage']}/generate", json={"preview": False}, headers=admin_client.api_headers).status_code == 409


def test_a08_seeded_knockout_byes_propagate_without_rally_results(app, admin_client, competition_case):
    case = competition_case
    configure_stage(app, case, "single_elimination", entry_count=5)
    response = admin_client.post(f"/api/v1/stages/{case['stage']}/generate", json={"preview": False}, headers=admin_client.api_headers)
    assert response.status_code == 201, response.get_json()
    matches = response.get_json()["matches"]
    assert len(matches) == 7
    byes = [m for m in matches if m["outcome"] == "bye"]
    assert len(byes) == 3
    assert all(m["status"] == "ended" and m["winner_id"] for m in byes)
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        for bye in byes:
            downstream = db.scalar(select(Match).where((Match.source_a_id == bye["id"]) | (Match.source_b_id == bye["id"])))
            assert downstream
            side = "a" if downstream.source_a_id == bye["id"] else "b"
            assert getattr(downstream, "entry_" + side + "_id") == bye["winner_id"]
            assert bye["state"]["sets"] == [{"a": 0, "b": 0, "complete": False, "winner": None}]


@pytest.mark.parametrize("invalid", [
    {"best_of": 2}, {"best_of": 5, "sets_to_win": 2}, {"mode": "fixed", "fixed_sets": 2, "draw_allowed": False},
    {"win_by": 0}, {"regular_target": 25, "cap": 20}, {"tie_breaks": ["standings_points", "team_name"]},
    {"tie_breaks": ["won", "won"]}, {"head_to_head": "alphabetic"},
])
def test_a06_invalid_competitive_policies_are_rejected(invalid):
    with pytest.raises(APIError):
        validate_rules(invalid)


@pytest.mark.parametrize(('invalid', 'partial'), [
    ({'automatic_approval': 'false'}, False),
    ({'automatic_approval': 'false'}, True),
    ({'draw_allowed': 'false'}, False),
    ({'include_forfeit_scores': 0}, False),
    ({'restart_tie_breaks': 1}, False),
    ({'cap': True, 'regular_target': 1, 'deciding_target': 1}, False),
    ({'win_points': True}, False),
    ({'loss_points': False}, False),
    ({'draw_points': True}, False),
    ({'forfeit_win_points': True}, False),
    ({'forfeit_loss_points': False}, False),
    ({'tie_breaks': [['standings_points']]}, False),
    ({'tie_breaks': [{'field': 'won'}]}, False),
    ({'mode': 'fixed', 'fixed_sets': 2, 'draw_allowed': 'false'}, False),
])
def test_rules_reject_truthy_strings_booleans_as_integers_and_nested_criteria(invalid, partial):
    with pytest.raises(APIError):
        validate_rules(invalid, partial=partial)


def test_rules_accept_explicit_boolean_policies_and_preserve_partial_override():
    policies = {'draw_allowed': True, 'include_forfeit_scores': False,
                'restart_tie_breaks': True, 'automatic_approval': False}
    partial = validate_rules(policies, partial=True)
    assert partial == policies and set(partial) == set(policies)
    assert all(type(value) is bool for value in partial.values())
    fixed = validate_rules({**policies, 'mode': 'fixed', 'fixed_sets': 2,
        'win_points': 2, 'loss_points': 0, 'draw_points': 1,
        'forfeit_win_points': 2, 'forfeit_loss_points': 0, 'cap': None})
    assert all(type(fixed[key]) is bool for key in policies)
    assert all(type(fixed[key]) is int for key in ('win_points', 'loss_points', 'draw_points',
                                                  'forfeit_win_points', 'forfeit_loss_points'))
    assert fixed['automatic_approval'] is False
    assert match_winner({'sets': [{'a': 25, 'b': 20, 'complete': True, 'winner': 'a'},
                                  {'a': 10, 'b': 15, 'complete': True, 'winner': 'b'}]}, fixed) == 'draw'


def test_a18_winning_margin_deciding_target_cap_and_fixed_draw():
    rules = validate_rules({"regular_target": 25, "deciding_target": 15, "win_by": 2})
    assert set_winner(25, 24, rules, 0) is None
    assert set_winner(26, 24, rules, 0) == "a"
    assert set_winner(15, 14, rules, 2) is None
    assert set_winner(16, 14, rules, 2) == "a"
    capped = validate_rules({"regular_target": 25, "cap": 27})
    assert set_winner(27, 26, capped, 0) == "a"
    fixed = validate_rules({"mode": "fixed", "fixed_sets": 2, "draw_allowed": True})
    assert match_winner({"sets": [{"a": 25, "b": 10, "winner": "a", "complete": True},
                                 {"a": 8, "b": 15, "winner": "b", "complete": True}]}, fixed) == "draw"


def test_a07_override_precedence_and_started_snapshot_ignore_later_stage_edits(app, admin_client, competition_case):
    case = competition_case
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        event, division, stage = db.get(Tournament, case["event"]), db.get(Division, case["division"]), db.get(Stage, case["stage"])
        division.rules, stage.rules = {"regular_target": 5}, {"regular_target": 3}
        match = Match(organization_id=case["org_id"], tournament_id=case["event"], division_id=case["division"], stage_id=case["stage"],
            entry_a_id=case["entries"][0], entry_b_id=case["entries"][1], rules={"regular_target": 4}, state={})
        db.add(match)
        db.flush()
        assert effective_rules(event, division, stage)["regular_target"] == 3
        assert effective_rules(event, division, stage, match)["regular_target"] == 4
        match_id = match.id
        db.commit()
    assert admin_client.post(f"/api/v1/matches/{match_id}/court", json={"court_id": case["court"]}, headers=admin_client.api_headers).status_code == 200
    qr = admin_client.post(f"/api/v1/matches/{match_id}/qr", json={}, headers=admin_client.api_headers).get_json()
    referee = app.test_client()
    admission = referee.post("/api/v1/referee/exchange", json={"token": qr["url"].split("#")[1]}).get_json()
    started = referee.post("/api/v1/referee/action", json={"client_action_id": str(uuid4()), "generation": admission["session"]["generation"],
        "expected_version": admission["match"]["version"], "type": "start", "payload": {}}, headers={"X-CSRF-Token": admission["csrf_token"]})
    assert started.status_code == 200, started.get_json()
    assert admin_client.patch(f"/api/v1/stages/{case['stage']}", json={"rules": {"regular_target": 8}}, headers=admin_client.api_headers).status_code == 200
    recovered = referee.get("/api/v1/referee/state").get_json()["match"]
    assert recovered["rules"]["regular_target"] == 4 and recovered["rule_snapshot"]


def test_a32_official_rankings_exclude_submitted_and_show_zero_quotient(app, admin_client, competition_case):
    case = competition_case
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        winner = finalize_snapshot(db, case, case["stage"], case["entries"][0], case["entries"][1])
        pending = finalize_snapshot(db, case, case["stage"], case["entries"][2], case["entries"][3])
        pending.result_status = "submitted"
        db.commit()
    official = admin_client.get(f"/api/v1/divisions/{case['division']}/standings", headers=admin_client.api_headers).get_json()
    provisional = admin_client.get(f"/api/v1/divisions/{case['division']}/standings?provisional=true", headers=admin_client.api_headers).get_json()
    assert official["sample_size"] == 1 and provisional["sample_size"] == 2
    row = next(r for r in official["rows"] if r["entry_id"] == case["entries"][0])
    assert row["set_quotient"] == "∞" and row["point_quotient"] == "∞" and row["standings_points"] == 2
    assert next(r for r in official["rows"] if r["entry_id"] == case["entries"][2])["played"] == 0


def test_a33_two_team_head_to_head_resolves_only_two_team_tie(app, competition_case):
    case = competition_case
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        division = db.get(Division, case["division"])
        division.rules = {"tie_breaks": ["head_to_head"], "head_to_head": "two_team"}
        for entry_id in case["entries"][2:]:
            db.get(Entry, entry_id).status = "withdrawn"
        finalize_snapshot(db, case, case["stage"], case["entries"][0], case["entries"][1])
        db.commit()
        rows = standings(division, case["stage"])["rows"]
        assert rows[0]["entry_id"] == case["entries"][0] and not rows[0]["tied"]
        db.get(Entry, case["entries"][2]).status = "active"
        db.commit()
        rows = standings(division, case["stage"])["rows"]
        assert len(rows) == 3 and all(r["rank"] == 1 and r["tied"] for r in rows)
        division.rules = {"tie_breaks": ["head_to_head"], "head_to_head": "mini_table"}
        db.commit()
        rows = standings(division, case["stage"])["rows"]
        assert rows[0]["entry_id"] == case["entries"][0] and rows[0]["rank"] == 1 and not rows[0]["tied"]


def test_a08_pool_qualification_blocks_unresolved_cycle_ties(app, admin_client, competition_case):
    case = competition_case
    configure_stage(app, case, "pools_knockout")
    generated = admin_client.post(f"/api/v1/stages/{case['stage']}/generate",
        json={"preview": False, "pool_count": 2, "qualify_per_pool": 2}, headers=admin_client.api_headers)
    assert generated.status_code == 201, generated.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        stage = db.get(Stage, case["stage"])
        stage.rules = {"tie_breaks": ["standings_points", "won", "head_to_head", "set_quotient", "point_quotient"], "head_to_head": "two_team"}
        matches = db.scalars(select(Match).where(Match.stage_id == stage.id)).all()
        pools = {}
        for entry_id in case["entries"]:
            entry = db.get(Entry, entry_id)
            pools.setdefault(entry.pool, []).append(entry_id)
        for match in matches:
            members = pools[match.pool]
            ai, bi = members.index(match.entry_a_id), members.index(match.entry_b_id)
            winning_id = match.entry_a_id if (ai - bi) % 3 == 1 else match.entry_b_id
            side = "a" if winning_id == match.entry_a_id else "b"
            match.state = {"sets": [{"a": 2 if side == "a" else 0, "b": 0 if side == "a" else 2,
                                     "complete": True, "winner": side} for _ in range(2)]}
            match.status, match.result_status, match.outcome, match.winner_id = "ended", "finalized", "normal", winning_id
        db.flush()
        from volleyball.competition import advance_results
        advance_results(matches[-1])
        db.commit()
        playoff = db.scalar(select(Stage).where(Stage.division_id == case["division"], Stage.format == "single_elimination"))
        assert playoff.settings["qualification_blocked"]
        first = db.scalars(select(Match).where(Match.stage_id == playoff.id, Match.round_number == 1)).all()
        assert first and all(m.entry_a_id is None and m.entry_b_id is None for m in first)


def test_a34_a35_correction_revisions_preview_and_preserve_started_downstream(app, admin_client, competition_case):
    case = competition_case
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        first = finalize_snapshot(db, case, case["stage"], case["entries"][0], case["entries"][1])
        downstream = Match(organization_id=case["org_id"], tournament_id=case["event"], division_id=case["division"],
            stage_id=case["stage"], entry_a_id=case["entries"][0], entry_b_id=case["entries"][2], source_a_id=first.id,
            status="in_progress", rule_snapshot=deepcopy(first.rule_snapshot), state={"sets": [{"a": 1, "b": 0, "complete": False, "winner": None}]})
        db.add(downstream)
        db.flush()
        first_id, downstream_id = first.id, downstream.id
        db.commit()
    correction = {"reason": "Correct verified administrative score", "sets": [{"a": 0, "b": 2}, {"a": 0, "b": 2}]}
    preview = admin_client.post(f"/api/v1/matches/{first_id}/correct", json={**correction, "preview": True}, headers=admin_client.api_headers)
    assert preview.status_code == 200 and preview.get_json()["preview"]["requires_adjudication"]
    applied = admin_client.post(f"/api/v1/matches/{first_id}/correct", json={**correction, "preview": False}, headers=admin_client.api_headers)
    assert applied.status_code == 200, applied.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        revised, played = db.get(Match, first_id), db.get(Match, downstream_id)
        assert revised.winner_id == case["entries"][1] and revised.result_revision == 2
        assert played.entry_a_id == case["entries"][0] and played.state["sets"][0]["a"] == 1
        assert played.settings["adjudication_required"]
        revisions = db.scalars(select(ResultRevision).where(ResultRevision.match_id == first_id).order_by(ResultRevision.revision)).all()
        assert len(revisions) == 2 and revisions[1].predecessor_id == revisions[0].id


@pytest.mark.parametrize("scope_type", ["tournament", "division", "stage"])
def test_historical_ranking_policy_requires_preview_explicit_recalculation_and_versioned_reason(app, admin_client, competition_case, scope_type):
    case = competition_case
    classes = {"tournament": Tournament, "division": Division, "stage": Stage}
    ids = {"tournament": case["event"], "division": case["division"], "stage": case["stage"]}
    scope_id, cls = ids[scope_type], classes[scope_type]
    proposed = validate_rules({"regular_target": 2, "deciding_target": 2, "win_points": 4}) if scope_type == "tournament" else {"win_points": 4}
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        finalized = finalize_snapshot(db, case, case["stage"], case["entries"][0], case["entries"][1])
        finalized_id = finalized.id
        original_rules = deepcopy(db.get(cls, scope_id).rules)
        db.commit()
    endpoint = f"/api/v1/{scope_type}s/{scope_id}"
    blocked = admin_client.patch(endpoint, json={"rules": proposed}, headers=admin_client.api_headers)
    assert blocked.status_code == 409 and blocked.get_json()["error"]["code"] == "ranking_review_required"
    no_reason = admin_client.patch(endpoint, json={"rules": proposed, "recalculate_history": True}, headers=admin_client.api_headers)
    assert no_reason.status_code == 409
    preview = admin_client.post("/api/v1/rules/preview", json={"scope_type": scope_type, "scope_id": scope_id, "rules": proposed}, headers=admin_client.api_headers)
    assert preview.status_code == 200, preview.get_json()
    before = next(r for r in preview.get_json()["before"][0]["rows"] if r["entry_id"] == case["entries"][0])
    after = next(r for r in preview.get_json()["after"][0]["rows"] if r["entry_id"] == case["entries"][0])
    assert before["standings_points"] == 2 and after["standings_points"] == 4
    with app.app_context():
        set_scope(case["org_id"])
        assert get_db().get(cls, scope_id).rules == original_rules
    changed = admin_client.patch(endpoint, json={"rules": proposed, "recalculate_history": True,
        "reason": "Reviewed historical award-policy recalculation"}, headers=admin_client.api_headers)
    assert changed.status_code == 200, changed.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        versions = db.scalars(select(RuleVersion).where(RuleVersion.scope_type == scope_type, RuleVersion.scope_id == scope_id)).all()
        assert versions and versions[-1].rules["win_points"] == 4
        assert versions[-1].reason == "Reviewed historical award-policy recalculation"
        assert db.get(Match, finalized_id).rule_snapshot["win_points"] == 2


def test_pool_correction_to_unresolved_tie_clears_prior_unstarted_qualifier(app, admin_client, competition_case):
    case = competition_case
    configure_stage(app, case, "pools_knockout")
    generated = admin_client.post(f"/api/v1/stages/{case['stage']}/generate",
        json={"preview": False, "pool_count": 2, "qualify_per_pool": 2}, headers=admin_client.api_headers)
    assert generated.status_code == 201
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        pools = {}
        for entry_id in case["entries"]:
            entry = db.get(Entry, entry_id)
            pools.setdefault(entry.pool, []).append(entry_id)
        pool_games = db.scalars(select(Match).where(Match.stage_id == case["stage"])).all()
        correction_id, correction_winner_side = None, None
        for game in pool_games:
            members = pools[game.pool]
            ai, bi = members.index(game.entry_a_id), members.index(game.entry_b_id)
            winner_side = "a" if ai < bi else "b"
            game.status, game.result_status, game.outcome, game.result_revision = "ended", "finalized", "normal", 1
            game.rule_snapshot = validate_rules({"regular_target": 2, "deciding_target": 2})
            game.winner_id = game.entry_a_id if winner_side == "a" else game.entry_b_id
            game.state = {"sets": [{"a": 2 if winner_side == "a" else 0, "b": 0 if winner_side == "a" else 2,
                                   "winner": winner_side, "complete": True} for _ in range(2)], "set_index": 1}
            db.add(ResultRevision(organization_id=case["org_id"], match_id=game.id, revision=1, sets=deepcopy(game.state["sets"]),
                winner_id=game.winner_id, status="finalized", reason="Explicit administrative test snapshot"))
            if game.pool == "A" and {ai, bi} == {0, 2}:
                correction_id = game.id
                correction_winner_side = "a" if ai == 2 else "b"
        db.flush()
        from volleyball.competition import advance_results
        advance_results(pool_games[-1])
        db.commit()
        playoff = db.scalar(select(Stage).where(Stage.division_id == case["division"], Stage.format == "single_elimination"))
        first = db.scalars(select(Match).where(Match.stage_id == playoff.id, Match.round_number == 1)).all()
        assert all(m.entry_a_id and m.entry_b_id for m in first)
        playoff_id = playoff.id
    changed_sets = [{"a": 2 if correction_winner_side == "a" else 0, "b": 0 if correction_winner_side == "a" else 2} for _ in range(2)]
    correction = admin_client.post(f"/api/v1/matches/{correction_id}/correct", json={"reason": "Correcting pool result creates an unresolved three-way tie",
        "sets": changed_sets, "preview": False}, headers=admin_client.api_headers)
    assert correction.status_code == 200, correction.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        first = db.scalars(select(Match).where(Match.stage_id == playoff_id, Match.round_number == 1)).all()
        for game in first:
            for side in ("a", "b"):
                source = game.settings.get("qualifier_" + side)
                if source.endswith(":A"):
                    assert getattr(game, "entry_" + side + "_id") is None
                else:
                    assert getattr(game, "entry_" + side + "_id")
        assert db.get(Stage, playoff_id).settings["qualification_blocked"]


def season_playoff_case(app, case, qualifier_count=2, source_format="league", cycle=False, point_difference=False):
    """Real season result snapshots; the final submitted game needs approval."""
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        season = db.get(Stage, case["stage"])
        season.format, season.position = source_format, 1
        if point_difference:
            season.rules = {"tie_breaks": ["standings_points", "point_difference"]}
        active_count = 3 if cycle else 4
        for entry_id in case["entries"][active_count:]:
            db.get(Entry, entry_id).status = "withdrawn"
        playoff = Stage(organization_id=case["org_id"], tournament_id=case["event"], division_id=case["division"],
            name="Season championship playoffs", format="single_elimination", position=2,
            settings={"source_stage_id": season.id, "qualifier_count": qualifier_count}, rules={})
        db.add(playoff)
        db.flush()
        pairings = [(a, b) for a in range(active_count) for b in range(a + 1, active_count)]
        source_matches = {}
        for index, (a, b) in enumerate(pairings):
            winner = "b" if cycle and (a, b) == (0, 2) else "a"
            status = "submitted" if index == len(pairings) - 1 else "finalized"
            match = finalize_snapshot(db, case, season.id, case["entries"][a], case["entries"][b],
                                      winner=winner, result_status=status)
            source_matches[(a, b)] = match.id
        db.commit()
        return {"playoff": playoff.id, "pending": source_matches[pairings[-1]], "sources": source_matches}


@pytest.mark.parametrize(("source_format", "qualifier_count"), [("league", 2), ("round_robin", 3)])
def test_league_playoff_placeholders_qualify_after_final_season_approval(app, admin_client, competition_case,
                                                                      source_format, qualifier_count):
    case = competition_case
    season = season_playoff_case(app, case, qualifier_count, source_format)
    generated = admin_client.post(f"/api/v1/stages/{season['playoff']}/generate", json={"preview": False},
                                  headers=admin_client.api_headers)
    assert generated.status_code == 201, generated.get_json()
    initial = generated.get_json()["matches"]
    assert len(initial) == (1 if qualifier_count == 2 else 3)
    assert all(match["team_a"] is None and match["team_b"] is None for match in initial)
    with app.app_context():
        set_scope(case["org_id"])
        first = get_db().scalars(select(Match).where(Match.stage_id == season["playoff"], Match.round_number == 1)).all()
        sources = {match.settings.get("qualifier_" + side) for match in first for side in ("a", "b")}
        assert sources - {None} == {f"qualifier:{rank}:season" for rank in range(1, qualifier_count + 1)}

    finalized = admin_client.post(f"/api/v1/matches/{season['pending']}/approve", json={}, headers=admin_client.api_headers)
    assert finalized.status_code == 200, finalized.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        games = db.scalars(select(Match).where(Match.stage_id == season["playoff"])).all()
        expected = {f"qualifier:{rank}:season": case["entries"][rank - 1] for rank in range(1, qualifier_count + 1)}
        for game in (match for match in games if match.round_number == 1):
            for side in ("a", "b"):
                key = game.settings.get("qualifier_" + side)
                assert getattr(game, "entry_" + side + "_id") == expected.get(key)
        assert not db.get(Stage, season["playoff"]).settings.get("qualification_blocked")
        if qualifier_count == 3:
            bye = next(game for game in games if game.outcome == "bye")
            assert bye.status == "ended" and bye.result_status == "finalized" and bye.winner_id == case["entries"][0]
            final = next(game for game in games if game.round_number == 2)
            side = "a" if final.source_a_id == bye.id else "b"
            assert getattr(final, "entry_" + side + "_id") == bye.winner_id


def test_league_playoff_unresolved_season_tie_blocks_qualification(app, admin_client, competition_case):
    case = competition_case
    season = season_playoff_case(app, case, qualifier_count=2, cycle=True)
    generated = admin_client.post(f"/api/v1/stages/{season['playoff']}/generate", json={"preview": False},
                                  headers=admin_client.api_headers)
    assert generated.status_code == 201
    finalized = admin_client.post(f"/api/v1/matches/{season['pending']}/approve", json={}, headers=admin_client.api_headers)
    assert finalized.status_code == 200, finalized.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        official = standings(db.get(Division, case["division"]), case["stage"])
        assert official["sample_size"] == 3 and all(row["tied"] for row in official["rows"])
        assert db.get(Stage, season["playoff"]).settings["qualification_blocked"]
        first = db.scalars(select(Match).where(Match.stage_id == season["playoff"], Match.round_number == 1)).all()
        assert first and all(game.entry_a_id is None and game.entry_b_id is None for game in first)
    adjudicated_order = [case["entries"][2], case["entries"][0], case["entries"][1]]
    resolution = admin_client.post(f"/api/v1/divisions/{case['division']}/resolve-tie", json={
        "reason": "Organizer reviewed the tied season qualification order", "order": adjudicated_order},
        headers=admin_client.api_headers)
    assert resolution.status_code == 200, resolution.get_json()
    assert [row["entry_id"] for row in resolution.get_json()["rows"]] == adjudicated_order
    assert not any(row["tied"] for row in resolution.get_json()["rows"])
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        assert not db.get(Stage, season["playoff"]).settings.get("qualification_blocked")
        first = db.scalars(select(Match).where(Match.stage_id == season["playoff"], Match.round_number == 1)).all()
        expected = {"qualifier:1:season": adjudicated_order[0], "qualifier:2:season": adjudicated_order[1]}
        for game in first:
            for side in ("a", "b"):
                assert getattr(game, "entry_" + side + "_id") == expected[game.settings["qualifier_" + side]]


def test_league_correction_preserves_started_semifinal_and_flags_new_qualifier(app, admin_client, competition_case):
    case = competition_case
    season = season_playoff_case(app, case, qualifier_count=3, point_difference=True)
    generated = admin_client.post(f"/api/v1/stages/{season['playoff']}/generate", json={"preview": False},
                                  headers=admin_client.api_headers)
    assert generated.status_code == 201
    assert admin_client.post(f"/api/v1/matches/{season['pending']}/approve", json={}, headers=admin_client.api_headers).status_code == 200
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        semifinal = db.scalar(select(Match).where(Match.stage_id == season["playoff"], Match.round_number == 1,
                Match.status == "scheduled"))
        assert semifinal.entry_a_id and semifinal.entry_b_id
        semifinal_id = semifinal.id
    from test_scoring import admit, act
    referee = admit(app, admin_client, semifinal_id, case["court"])
    assert act(referee, "start").status_code == 200
    assert act(referee, "point", {"team": "a"}).status_code == 200
    before_state = deepcopy(referee.match["state"])
    played_teams = (referee.match["team_a"]["id"], referee.match["team_b"]["id"])
    changed = admin_client.post(f"/api/v1/matches/{season['sources'][(1, 3)]}/correct", json={"preview": False,
        "reason": "Verified season score admits a different second qualifier", "sets": [{"a": 0, "b": 4}, {"a": 0, "b": 4}]},
        headers=admin_client.api_headers)
    assert changed.status_code == 200, changed.get_json()
    with app.app_context():
        set_scope(case["org_id"])
        db = get_db()
        official = standings(db.get(Division, case["division"]), case["stage"])
        assert official["rows"][1]["entry_id"] == case["entries"][3]
        assert not any(row["tied"] for row in official["rows"][:3])
        played = db.get(Match, semifinal_id)
        assert played.status == "in_progress"
        assert (played.entry_a_id, played.entry_b_id) == played_teams
        assert played.state == before_state
        assert played.settings["adjudication_required"] is True
