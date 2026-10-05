"""Deterministic transaction races and recovery from immutable PostgreSQL data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
import threading
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from volleyball.db import get_db, set_scope
from volleyball.models import (CourtAssignment, Division, Entry, Match, MatchEvent,
    ResultRevision, ScorerSession, Stage, Tournament)
from volleyball.services.rankings import compare_pools
from volleyball.services.recovery import journal_projection, rebuild
from volleyball.services.rules import validate_rules

# Reuse the real PostgreSQL/RLS setup and actual QR admission API.
from test_scoring import game_case, admit, act


def clone_referee(app, referee):
    with referee.session_transaction() as cookie:
        saved_cookie = dict(cookie)
    cloned = app.test_client()
    with cloned.session_transaction() as cookie:
        cookie.update(saved_cookie)
    cloned.csrf = referee.csrf
    cloned.generation = referee.generation
    cloned.match = deepcopy(referee.match)
    return cloned


def test_simultaneous_stale_score_writes_commit_one(app, admin_client, game_case):
    case = game_case
    referee = admit(app, admin_client, case['matches'][0], case['courts'][0])
    assert act(referee, 'start').status_code == 200
    clients = [clone_referee(app, referee) for _ in range(2)]
    barrier = threading.Barrier(2)
    version = referee.match['version']

    def point(index):
        barrier.wait(timeout=10)
        return act(clients[index], 'point', {'team': 'a' if index == 0 else 'b'}, expected=version)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(point, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    failed = next(response for response in responses if response.status_code == 409)
    assert failed.json['error']['code'] == 'version_conflict'
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        match = db.get(Match, case['matches'][0])
        points = db.scalars(select(MatchEvent).where(MatchEvent.match_id == match.id, MatchEvent.type == 'point')).all()
        assert len(points) == 1
        assert match.version == version + 1
        assert match.state['sets'][0]['a'] + match.state['sets'][0]['b'] == 1
        assert points[0].receipt['version'] == match.version


def test_waiting_takeover_cannot_resurrect_revoked_phone(app, admin_client, game_case):
    """Pause after authority lookup, let another phone revoke it, then lock."""
    case = game_case
    old = admit(app, admin_client, case['matches'][0], case['courts'][0])
    assert act(old, 'start').status_code == 200
    assert act(old, 'point', {'team': 'a'}).status_code == 200
    with old.session_transaction() as cookie:
        old_session_id = cookie['referee_session_id']

    qr = admin_client.post(f"/api/v1/matches/{case['matches'][0]}/qr", json={}, headers=admin_client.api_headers)
    assert qr.status_code == 200
    new = app.test_client()
    admission = new.post('/api/v1/referee/exchange', json={'token': qr.json['url'].split('#')[1]})
    assert admission.status_code == 200
    new_csrf = admission.json['csrf_token']
    with new.session_transaction() as cookie:
        new_session_id = cookie['referee_session_id']

    reached_lock, allow_lock = threading.Event(), threading.Event()
    engine = app.extensions['db_engine']

    def delay_old_match_lock(conn, cursor, statement, parameters, context, executemany):
        if (threading.current_thread().name.startswith('waiting-old-takeover')
                and 'FROM matches' in statement and 'FOR UPDATE' in statement):
            reached_lock.set()
            assert allow_lock.wait(timeout=15), 'Takeover race barrier timed out'

    event.listen(engine, 'before_cursor_execute', delay_old_match_lock)
    try:
        with ThreadPoolExecutor(1, thread_name_prefix='waiting-old-takeover') as pool:
            waiting = pool.submit(old.post, '/api/v1/referee/resume',
                json={'takeover': True}, headers={'X-CSRF-Token': old.csrf})
            try:
                assert reached_lock.wait(timeout=10), 'Old request did not reach the locked game query'
                takeover = new.post('/api/v1/referee/resume', json={'takeover': True},
                                    headers={'X-CSRF-Token': new_csrf})
                assert takeover.status_code == 200, takeover.json
            finally:
                allow_lock.set()
            rejected = waiting.result(timeout=15)
    finally:
        allow_lock.set()
        event.remove(engine, 'before_cursor_execute', delay_old_match_lock)

    assert rejected.status_code == 401
    assert rejected.json['error']['code'] == 'referee_access'
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        old_row, new_row = db.get(ScorerSession, old_session_id), db.get(ScorerSession, new_session_id)
        match = db.get(Match, case['matches'][0])
        assert old_row.revoked_at is not None
        assert new_row.revoked_at is None
        assert new_row.generation == match.generation == takeover.json['session']['generation']
        assert match.state['sets'][0]['a'] == 1
        active = db.scalars(select(ScorerSession).where(ScorerSession.match_id == match.id,
                    ScorerSession.revoked_at.is_(None), ScorerSession.generation >= 0)).all()
        assert [row.id for row in active] == [new_session_id]


def test_simultaneous_starts_of_shared_team_across_events(app, admin_client, game_case):
    case = game_case
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        tournament = Tournament(organization_id=case['org_id'], name='Shared team event',
            slug='shared-' + uuid4().hex[:12], start_date=date.today(), end_date=date.today() + timedelta(days=2),
            timezone='America/New_York', status='published', visibility='private', rules=validate_rules({}))
        db.add(tournament)
        db.flush()
        division = Division(organization_id=case['org_id'], tournament_id=tournament.id, name='Shared identity')
        db.add(division)
        db.flush()
        stage = Stage(organization_id=case['org_id'], tournament_id=tournament.id, division_id=division.id,
                      name='Shared team round', format='league')
        db.add(stage)
        db.flush()
        entries = []
        for index in (0, 2):
            existing = db.get(Entry, case['entries'][index])
            entry = Entry(organization_id=case['org_id'], tournament_id=tournament.id, division_id=division.id,
                team_id=existing.team_id, name=existing.name + ' different event', seed=index + 1)
            db.add(entry)
            db.flush()
            entries.append(entry)
        match = Match(organization_id=case['org_id'], tournament_id=tournament.id, division_id=division.id,
            stage_id=stage.id, entry_a_id=entries[0].id, entry_b_id=entries[1].id, state={})
        db.add(match)
        db.flush()
        second_match_id = match.id
        db.commit()

    clients = [admit(app, admin_client, case['matches'][0], case['courts'][0]),
               admit(app, admin_client, second_match_id, case['courts'][1])]
    barrier = threading.Barrier(2)

    def start(index):
        barrier.wait(timeout=10)
        return act(clients[index], 'start')

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(start, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 409).json['error']['code'] == 'team_conflict'
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        ids = [case['matches'][0], second_match_id]
        matches = db.scalars(select(Match).where(Match.id.in_(ids))).all()
        assert sorted(match.status for match in matches) == ['in_progress', 'ready']
        reservations = db.scalars(select(CourtAssignment).where(CourtAssignment.match_id.in_(ids),
            CourtAssignment.released_at.is_(None))).all()
        assert sorted(reservation.status for reservation in reservations) == ['occupied', 'reserved']


def test_simultaneous_starts_of_same_assigned_referee(app, admin_client, game_case):
    case = game_case
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        for match_id in case['matches']:
            db.get(Match, match_id).referee_name = 'One explicitly assigned referee'
        db.commit()
    clients = [admit(app, admin_client, match_id, court_id)
               for match_id, court_id in zip(case['matches'], case['courts'])]
    barrier = threading.Barrier(2)

    def start(index):
        barrier.wait(timeout=10)
        return act(clients[index], 'start')

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(start, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 409).json['error']['code'] == 'referee_conflict'
    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        matches = db.scalars(select(Match).where(Match.id.in_(case['matches']))).all()
        assert sorted(match.status for match in matches) == ['in_progress', 'ready']
        active = db.scalars(select(CourtAssignment).where(CourtAssignment.match_id.in_(case['matches']),
            CourtAssignment.released_at.is_(None))).all()
        assert sorted(row.status for row in active) == ['occupied', 'reserved']


def test_rebuild_latest_saved_scores_retains_result_revision_chain(app, admin_client, game_case):
    case = game_case
    referee = admit(app, admin_client, case['matches'][0], case['courts'][0])
    assert act(referee, 'start').status_code == 200
    assert act(referee, 'timeout', {'team': 'b'}).status_code == 200
    for set_number in range(2):
        assert act(referee, 'point', {'team': 'a'}).status_code == 200
        assert act(referee, 'point', {'team': 'a'}).status_code == 200
        if set_number == 0:
            assert act(referee, 'next_set').status_code == 200
    assert act(referee, 'end').status_code == 200
    approval = admin_client.post(f"/api/v1/matches/{case['matches'][0]}/approve", json={}, headers=admin_client.api_headers)
    assert approval.status_code == 200
    correction = admin_client.post(f"/api/v1/matches/{case['matches'][0]}/correct", json={
        'preview': False, 'reason': 'Reviewed signed score sheet', 'sets': [{'a': 0, 'b': 2}, {'a': 0, 'b': 2}]},
        headers=admin_client.api_headers)
    assert correction.status_code == 200, correction.json

    with app.app_context():
        set_scope(case['org_id'])
        db = get_db()
        match = db.get(Match, case['matches'][0])
        expected_state, expected_rules = deepcopy(match.state), deepcopy(match.rule_snapshot)
        expected_version, expected_revision = match.version, match.result_revision
        revisions = db.scalars(select(ResultRevision).where(ResultRevision.match_id == match.id)
                              .order_by(ResultRevision.revision)).all()
        before_revisions = [(row.id, row.revision, deepcopy(row.sets), row.predecessor_id, row.status) for row in revisions]
        assert len(revisions) == 3
        assert revisions[-1].sets == expected_state['sets']
        assert revisions[-1].predecessor_id == revisions[-2].id
        assert revisions[-2].predecessor_id == revisions[-3].id
        events = db.scalars(select(MatchEvent).where(MatchEvent.match_id == match.id).order_by(MatchEvent.sequence)).all()
        before_events = [(row.id, row.client_action_id, deepcopy(row.receipt), deepcopy(row.payload)) for row in events]
        # Simulate projection damage, while journals and result revisions remain immutable.
        match.state = {'sets': [{'a': 99, 'b': 0}], 'timeouts': {'a': 0, 'b': 0}}
        match.rule_snapshot = {'regular_target': 99}
        db.commit()
        assert journal_projection(match)['consistent'] is False
        projection = rebuild(match)
        assert projection['rebuildable'] is True
        db.commit()
        db.expire_all()
        assert match.state == expected_state
        assert match.rule_snapshot == expected_rules
        assert match.version == expected_version
        assert match.result_revision == expected_revision
        assert match.winner_id == case['entries'][1]
        assert match.status == 'ended' and match.result_status == 'finalized'
        assert journal_projection(match)['consistent'] is True
        assert [(row.id, row.revision, row.sets, row.predecessor_id, row.status) for row in revisions] == before_revisions
        assert [(row.id, row.client_action_id, row.receipt, row.payload) for row in events] == before_events


@pytest.mark.parametrize(('policy', 'first', 'metric'), [
    ('points_per_match', 'shorter-pool', 2), ('total_points', 'longer-pool', 6),
])
def test_cross_pool_unequal_games_use_selected_policy(policy, first, metric):
    rows = [{'entry_id': 'longer-pool', 'seed': 1, 'played': 4, 'standings_points': 6, 'won': 3},
            {'entry_id': 'shorter-pool', 'seed': 2, 'played': 2, 'standings_points': 4, 'won': 2}]
    rules = validate_rules({'cross_pool': policy, 'tie_breaks': ['standings_points', 'won']})
    ranked = compare_pools(rows, rules)
    assert ranked[0]['entry_id'] == first
    assert ranked[0]['comparison_metrics'][0] == metric
    assert ranked[0]['comparison_rank'] == 1
    assert all(not row['comparison_tied'] for row in ranked)
    assert rows[0]['standings_points'] == 6  # Comparing does not replace official totals.


def test_cross_pool_equal_normalized_points_remain_unresolved():
    rows = [{'entry_id': 'longer-pool', 'seed': 1, 'played': 4, 'standings_points': 8},
            {'entry_id': 'shorter-pool', 'seed': 2, 'played': 2, 'standings_points': 4}]
    ranked = compare_pools(rows, validate_rules({'cross_pool': 'points_per_match', 'tie_breaks': ['standings_points']}))
    assert [row['comparison_rank'] for row in ranked] == [1, 1]
    assert all(row['comparison_tied'] for row in ranked)
    assert all(row['comparison_metrics'] == [2] for row in ranked)


def test_cross_pool_manual_resolution_requires_reason_and_full_order():
    rows = [{'entry_id': 'longer-pool', 'seed': 1, 'played': 4, 'standings_points': 8},
            {'entry_id': 'shorter-pool', 'seed': 2, 'played': 2, 'standings_points': 4}]
    rules = validate_rules({'cross_pool': 'points_per_match', 'tie_breaks': ['standings_points', 'manual']})
    assert all(row['comparison_tied'] for row in compare_pools(rows, rules))
    assert all(row['comparison_tied'] for row in compare_pools(rows, rules, {'order': ['shorter-pool', 'longer-pool']}))
    assert all(row['comparison_tied'] for row in compare_pools(rows, rules,
        {'reason': 'Reviewed organizer adjudication', 'order': ['shorter-pool']}))
    ranked = compare_pools(rows, rules, {'reason': 'Reviewed organizer adjudication',
                                      'order': ['shorter-pool', 'longer-pool']})
    assert [row['entry_id'] for row in ranked] == ['shorter-pool', 'longer-pool']
    assert [row['comparison_rank'] for row in ranked] == [1, 2]
    assert all(not row['comparison_tied'] and row['comparison_resolution_reason'] == 'Reviewed organizer adjudication'
               for row in ranked)
