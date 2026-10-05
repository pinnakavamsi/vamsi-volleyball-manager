"""Public metadata freshness without invalidating overviews on every rally."""
import hashlib
import json
from sqlalchemy import text
from ..db import get_db


_SIGNATURE=text("""
WITH target AS (
    SELECT id, organization_id, updated_at, settings
    FROM tournaments WHERE id=CAST(:tournament_id AS uuid)
), associated_venues AS (
    SELECT tv.venue_id FROM tournament_venues tv JOIN target t ON tv.tournament_id=t.id WHERE tv.active
    UNION
    SELECT CAST(chosen.venue_id AS uuid) FROM target t,
        jsonb_array_elements_text(COALESCE(t.settings->'venue_ids','[]'::jsonb)) AS chosen(venue_id)
    UNION
    SELECT c.venue_id FROM courts c JOIN court_assignments a ON a.court_id=c.id
        JOIN matches m ON m.id=a.match_id JOIN target t ON m.tournament_id=t.id
), associated_courts AS (
    SELECT c.id FROM courts c WHERE c.venue_id IN (SELECT venue_id FROM associated_venues)
)
SELECT
    (SELECT updated_at::text FROM target) AS tournament,
    (SELECT o.updated_at::text FROM organizations o JOIN target t ON o.id=t.organization_id) AS organization,
    (SELECT concat(count(*),':',max(d.updated_at)) FROM divisions d JOIN target t ON d.tournament_id=t.id) AS divisions,
    (SELECT concat(count(*),':',max(s.updated_at)) FROM stages s JOIN target t ON s.tournament_id=t.id) AS stages,
    (SELECT concat(count(*),':',max(e.updated_at)) FROM entries e JOIN target t ON e.tournament_id=t.id) AS entries,
    (SELECT md5(COALESCE(string_agg(a.id::text||':'||a.updated_at::text,',' ORDER BY a.id),''))
        FROM announcements a JOIN target t ON a.tournament_id=t.id
        WHERE a.visibility='public' AND a.publish_at<=now() AND (a.expires_at IS NULL OR a.expires_at>now())) AS announcements,
    (SELECT concat(count(*),':',max(v.updated_at)) FROM venues v WHERE v.id IN (SELECT venue_id FROM associated_venues)) AS venues,
    (SELECT concat(count(*),':',max(c.updated_at)) FROM courts c WHERE c.id IN (SELECT id FROM associated_courts)) AS courts,
    (SELECT md5(COALESCE(string_agg(m.id::text||':'||COALESCE(m.rules::text,'{}')||':'||COALESCE(m.rule_snapshot::text,'{}'),',' ORDER BY m.id),''))
        FROM matches m JOIN target t ON m.tournament_id=t.id) AS match_rules
""")


def public_configuration_version(tournament):
    """One aggregate SQL statement; score/version/state/time updates are excluded."""
    signature=get_db().execute(_SIGNATURE,{'tournament_id':tournament.id}).mappings().one()
    return hashlib.sha256(json.dumps(dict(signature),sort_keys=True,separators=(',',':')).encode()).hexdigest()
