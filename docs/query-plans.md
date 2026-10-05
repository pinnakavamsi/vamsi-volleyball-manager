# Measured local PostgreSQL query plans

Collected after the isolated load window using the nonsuperuser runtime role with RLS enabled. The transaction organization scope is set through the application helper. IDs and throttle hashes are replaced with parameter labels.

Measured at: 2026-10-05T18:46:35.917457+00:00. Schema revision: `20261005_relationship_guards`.

Fixture: two isolated organizations, 25 live games, accepted scoring journals from the measured baseline, and 1,000 explicitly simulated finalized historical matches plus result revisions. Historical rows are added after the load measurement only to exercise reports; they do not change the public workload. These small local tables and warm buffers do not establish production query costs.

## Tenant match projection

```sql
SELECT id,version,status,state FROM matches WHERE organization_id=:org AND tournament_id=:event ORDER BY planned_at,round_number LIMIT 1000
```

```text
Limit  (cost=8.19..8.24 rows=2 width=132) (actual time=0.043..0.044 rows=13.00 loops=1)
  Output: id, version, status, state, planned_at, round_number
  Buffers: shared hit=37
  ->  Incremental Sort  (cost=8.19..8.24 rows=2 width=132) (actual time=0.043..0.043 rows=13.00 loops=1)
        Output: id, version, status, state, planned_at, round_number
        Sort Key: matches.planned_at, matches.round_number
        Presorted Key: matches.planned_at
        Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 31kB  Peak Memory: 31kB
        Buffers: shared hit=37
        ->  Index Scan using ix_match_scope_time on public.matches  (cost=0.15..8.18 rows=1 width=132) (actual time=0.012..0.033 rows=13.00 loops=1)
              Output: id, version, status, state, planned_at, round_number
              Index Cond: ((matches.organization_id = '<ORG>'::uuid) AND (matches.tournament_id = '<EVENT>'::uuid))
              Filter: ((matches.organization_id)::text = current_setting('app.organization_id'::text, true))
              Index Searches: 1
              Buffers: shared hit=31
Planning:
  Buffers: shared hit=8
Planning Time: 0.046 ms
Execution Time: 0.050 ms
```

## Idempotency receipt by match and action UUID

```sql
SELECT sequence,new_version,receipt FROM match_events WHERE match_id=:match AND client_action_id=:action LIMIT 1
```

```text
Limit  (cost=0.28..8.31 rows=1 width=40) (actual time=0.004..0.004 rows=1.00 loops=1)
  Output: sequence, new_version, receipt
  Buffers: shared hit=3
  ->  Index Scan using match_events_match_id_client_action_id_key on public.match_events  (cost=0.28..8.31 rows=1 width=40) (actual time=0.004..0.004 rows=1.00 loops=1)
        Output: sequence, new_version, receipt
        Index Cond: ((match_events.match_id = '<MATCH>'::uuid) AND (match_events.client_action_id = '<ACTION>'::uuid))
        Filter: ((match_events.organization_id)::text = current_setting('app.organization_id'::text, true))
        Index Searches: 1
        Buffers: shared hit=3
Planning Time: 0.019 ms
Execution Time: 0.007 ms
```

## Scoped finalized results and latest immutable revision

```sql
SELECT m.id,m.actual_end,m.winner_id,r.revision,r.sets FROM matches m JOIN result_revisions r ON r.match_id=m.id AND r.organization_id=m.organization_id AND r.revision=m.result_revision WHERE m.organization_id=:org AND m.tournament_id=:event AND m.result_status='finalized' ORDER BY m.actual_end DESC LIMIT 100
```

```text
Limit  (cost=24.54..24.54 rows=1 width=76) (actual time=0.533..0.538 rows=100.00 loops=1)
  Output: m.id, m.actual_end, m.winner_id, r.revision, r.sets
  Buffers: shared hit=1585
  ->  Sort  (cost=24.54..24.54 rows=1 width=76) (actual time=0.533..0.535 rows=100.00 loops=1)
        Output: m.id, m.actual_end, m.winner_id, r.revision, r.sets
        Sort Key: m.actual_end DESC
        Sort Method: top-N heapsort  Memory: 50kB
        Buffers: shared hit=1585
        ->  Nested Loop  (cost=0.43..24.53 rows=1 width=76) (actual time=0.006..0.506 rows=500.00 loops=1)
              Output: m.id, m.actual_end, m.winner_id, r.revision, r.sets
              Inner Unique: true
              Buffers: shared hit=1585
              ->  Index Scan using ix_match_scope_time on public.matches m  (cost=0.15..8.19 rows=1 width=60) (actual time=0.002..0.196 rows=500.00 loops=1)
                    Output: m.tournament_id, m.division_id, m.stage_id, m.entry_a_id, m.entry_b_id, m.source_a_id, m.source_b_id, m.round_number, m.pool, m.planned_at, m.planned_duration, m.actual_start, m.actual_end, m.status, m.version, m.generation, m.rules, m.rule_snapshot, m.state, m.winner_id, m.outcome, m.result_status, m.result_revision, m.referee_name, m.settings, m.organization_id, m.id, m.created_at, m.updated_at
                    Index Cond: ((m.organization_id = '<ORG>'::uuid) AND (m.tournament_id = '<EVENT>'::uuid))
                    Filter: (((m.result_status)::text = 'finalized'::text) AND ((m.organization_id)::text = current_setting('app.organization_id'::text, true)))
                    Index Searches: 1
                    Buffers: shared hit=85
              ->  Index Scan using result_revisions_match_id_revision_key on public.result_revisions r  (cost=0.28..8.31 rows=1 width=68) (actual time=0.000..0.000 rows=1.00 loops=500)
                    Output: r.match_id, r.revision, r.sets, r.winner_id, r.outcome, r.status, r.approved_by, r.approved_at, r.reason, r.predecessor_id, r.organization_id, r.id, r.created_at, r.updated_at
                    Index Cond: ((r.match_id = m.id) AND (r.revision = m.result_revision))
                    Filter: ((r.organization_id = '<ORG>'::uuid) AND ((r.organization_id)::text = current_setting('app.organization_id'::text, true)))
                    Index Searches: 500
                    Buffers: shared hit=1500
Planning:
  Buffers: shared hit=60
Planning Time: 0.100 ms
Execution Time: 0.547 ms
```

## Persisted public rate-limit lookup

```sql
SELECT count(*) FROM identity_tokens WHERE kind='public_limit' AND data ? 'throttle_key' AND data->>'throttle_key'=:key AND expires_at>now()
```

```text
Aggregate  (cost=78.73..78.74 rows=1 width=8) (actual time=0.028..0.028 rows=1.00 loops=1)
  Output: count(*)
  Buffers: shared hit=18
  ->  Index Scan using ix_identity_throttle on public.identity_tokens  (cost=0.41..78.65 rows=31 width=0) (actual time=0.007..0.026 rows=15.00 loops=1)
        Output: kind, token_hash, email, user_id, organization_id, data, expires_at, used_at, revoked_at, id, created_at, updated_at
        Index Cond: (((identity_tokens.kind)::text = 'public_limit'::text) AND ((identity_tokens.data ->> 'throttle_key'::text) = '<KEY>'::text) AND (identity_tokens.expires_at > now()))
        Index Searches: 1
        Buffers: shared hit=18
Planning Time: 0.036 ms
Execution Time: 0.033 ms
```
