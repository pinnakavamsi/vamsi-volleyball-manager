"""Runtime readiness and authentication throttle lookup."""
from alembic import op
revision='20261005_runtime_observability'
down_revision='20261005_scope_security'
branch_labels=None
depends_on=None
def upgrade():
    op.execute("CREATE INDEX ix_identity_throttle ON identity_tokens (kind,(data->>'throttle_key'),expires_at) WHERE data ? 'throttle_key'")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='volleyball_app') THEN GRANT SELECT ON alembic_version TO volleyball_app; END IF; END $$")
def downgrade():
    op.execute('DROP INDEX ix_identity_throttle')
