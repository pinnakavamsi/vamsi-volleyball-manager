"""Permanently close first-access setup after initial platform administration."""
from alembic import op
import sqlalchemy as sa

revision = '20261011_first_run_setup'
down_revision = '20261006_organization_support'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('platform_setup',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint('id = 1', name='ck_platform_setup_singleton'))
    # Existing installations remain closed, including a historical administrator
    # that was subsequently deactivated or removed from its platform role.
    op.execute("""INSERT INTO platform_setup(id)
        SELECT 1 WHERE EXISTS (SELECT 1 FROM users WHERE platform_admin)
        OR EXISTS (SELECT 1 FROM audit_events
          WHERE actor_type IN ('platform_admin','platform_administrator')
          OR action='platform.bootstrap')""")
    op.execute("""CREATE FUNCTION close_platform_setup() RETURNS trigger
        LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN INSERT INTO platform_setup(id) VALUES(1) ON CONFLICT DO NOTHING;
        RETURN NEW; END $$""")
    op.execute("""CREATE TRIGGER close_first_access_setup AFTER INSERT OR UPDATE OF platform_admin
        ON users FOR EACH ROW WHEN (NEW.platform_admin = true)
        EXECUTE FUNCTION close_platform_setup()""")
    op.execute("""CREATE TRIGGER immutable_setup_completion BEFORE UPDATE OR DELETE
        ON platform_setup FOR EACH ROW EXECUTE FUNCTION prevent_journal_changes()""")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='volleyball_app') THEN
        GRANT SELECT,INSERT ON platform_setup TO volleyball_app;
        REVOKE UPDATE,DELETE,TRUNCATE ON platform_setup FROM volleyball_app;
        END IF; END $$""")


def downgrade():
    raise RuntimeError('First-access completion is permanent; use a reviewed installation restore.')
