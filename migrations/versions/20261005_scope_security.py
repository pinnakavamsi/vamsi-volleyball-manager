"""Runtime isolation, credential lookup boundary and append-only journals."""
from alembic import op
revision='20261005_scope_security'
down_revision='27b29dd3676c'
branch_labels=None
depends_on=None
TABLES=['teams','venues','courts','tournaments','divisions','stages','entries','matches','court_assignments','match_events','result_revisions','audit_events','outbox_events','preferences','announcements','jobs','weather_cache']
def upgrade():
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        if table=='audit_events':
            op.execute("CREATE POLICY organization_scope ON audit_events USING (organization_id::text = current_setting('app.organization_id', true) OR (organization_id IS NULL AND current_setting('app.platform_admin', true)='true')) WITH CHECK (organization_id IS NULL OR organization_id::text = current_setting('app.organization_id', true))")
        else:
            op.execute(f"CREATE POLICY organization_scope ON {table} USING (organization_id::text = current_setting('app.organization_id', true)) WITH CHECK (organization_id::text = current_setting('app.organization_id', true))")
    op.execute("""CREATE FUNCTION resolve_public_tournament(public_slug text) RETURNS TABLE(tournament_id uuid, organization_id uuid)
       LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
       SELECT t.id,t.organization_id FROM tournaments t JOIN organizations o ON o.id=t.organization_id
       WHERE t.slug=public_slug AND t.visibility='public' AND t.status<>'draft' AND o.active=true $$""")
    op.execute('REVOKE ALL ON FUNCTION resolve_public_tournament(text) FROM PUBLIC')
    op.execute("""CREATE FUNCTION prevent_journal_changes() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN RAISE EXCEPTION 'Journal records are append-only'; END $$""")
    for table in ['audit_events','match_events']:
        op.execute(f'CREATE TRIGGER immutable_journal BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_journal_changes()')
    op.execute("ALTER TABLE matches ADD CONSTRAINT uq_match_competition UNIQUE (id,division_id,tournament_id,organization_id)")
    for side in ['a','b']:
        op.execute(f'ALTER TABLE matches ADD CONSTRAINT fk_source_{side}_scope FOREIGN KEY (source_{side}_id,division_id,tournament_id,organization_id) REFERENCES matches(id,division_id,tournament_id,organization_id)')
    op.execute('ALTER TABLE matches ADD CONSTRAINT ck_source_acyclic_self CHECK ((source_a_id IS NULL OR source_a_id<>id) AND (source_b_id IS NULL OR source_b_id<>id))')
    op.execute('ALTER TABLE matches ADD CONSTRAINT ck_winner_participant CHECK (winner_id IS NULL OR winner_id=entry_a_id OR winner_id=entry_b_id)')
    op.execute("ALTER TABLE matches ADD CONSTRAINT ck_match_lifecycle CHECK (status IN ('scheduled','ready','in_progress','paused','ended','postponed','abandoned','cancelled'))")
    op.execute("ALTER TABLE tournaments ADD CONSTRAINT ck_tournament_lifecycle CHECK (status IN ('draft','published','active','completed','archived'))")
    op.execute('ALTER TABLE match_events ADD CONSTRAINT ck_event_version CHECK (sequence>0 AND new_version>expected_version)')
    # Bootstrap establishes a dedicated nonowner runtime role. Migrations retain ownership.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='volleyball_app') THEN
       GRANT USAGE ON SCHEMA public TO volleyball_app;
       GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO volleyball_app;
       REVOKE UPDATE,DELETE ON audit_events,match_events FROM volleyball_app;
       REVOKE ALL ON alembic_version FROM volleyball_app;
       GRANT EXECUTE ON FUNCTION resolve_public_tournament(text) TO volleyball_app;
       END IF; END $$""")
def downgrade():
    raise RuntimeError('Security/journal migration requires a reviewed restore, not destructive downgrade.')
