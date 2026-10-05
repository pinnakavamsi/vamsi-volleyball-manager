"""Complete organization boundaries on journal, rule and operational links."""
from alembic import op
revision='20261005_relationship_guards'
down_revision='20261005_rule_history_venues'
branch_labels=None
depends_on=None
LINKS=[('weather_cache','venue_id','venues'),('announcements','tournament_id','tournaments'),('rule_versions','tournament_id','tournaments'),('rule_versions','division_id','divisions'),('rule_versions','stage_id','stages'),('outbox_events','tournament_id','tournaments'),('outbox_events','match_id','matches'),('result_revisions','winner_id','entries'),('audit_events','session_id','scorer_sessions')]
def upgrade():
    for table,column,target in LINKS:
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{column}_organization FOREIGN KEY ({column},organization_id) REFERENCES {target}(id,organization_id)')
    op.execute('ALTER TABLE qr_grants ADD CONSTRAINT uq_grant_match_scope UNIQUE (id,match_id,organization_id)')
    op.execute('ALTER TABLE scorer_sessions ADD CONSTRAINT uq_scorer_match_scope UNIQUE (id,match_id,organization_id)')
    op.execute('ALTER TABLE scorer_sessions ADD CONSTRAINT fk_scorer_grant_scope FOREIGN KEY (grant_id,match_id,organization_id) REFERENCES qr_grants(id,match_id,organization_id)')
    op.execute('ALTER TABLE match_events ADD CONSTRAINT fk_event_session_match FOREIGN KEY (session_id,match_id,organization_id) REFERENCES scorer_sessions(id,match_id,organization_id)')
def downgrade():raise RuntimeError('Relationship guards are retained; use a reviewed recovery if needed.')
