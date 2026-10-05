"""Versioned rule history and relational venue associations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='20261005_rule_history_venues'
down_revision='20261005_runtime_observability'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('rule_versions',sa.Column('id',sa.UUID(),primary_key=True),sa.Column('organization_id',sa.UUID(),sa.ForeignKey('organizations.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('tournament_id',sa.UUID(),sa.ForeignKey('tournaments.id'),nullable=False),sa.Column('division_id',sa.UUID(),sa.ForeignKey('divisions.id')),sa.Column('stage_id',sa.UUID(),sa.ForeignKey('stages.id')),sa.Column('scope_type',sa.String(20),nullable=False),sa.Column('scope_id',sa.UUID(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),sa.Column('rules',postgresql.JSONB(),nullable=False),sa.Column('reason',sa.Text()),sa.Column('created_by',sa.UUID(),sa.ForeignKey('users.id')),sa.UniqueConstraint('id','organization_id'),sa.UniqueConstraint('scope_type','scope_id','version'))
    op.create_table('tournament_venues',sa.Column('id',sa.UUID(),primary_key=True),sa.Column('organization_id',sa.UUID(),sa.ForeignKey('organizations.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('tournament_id',sa.UUID(),nullable=False),sa.Column('venue_id',sa.UUID(),nullable=False),sa.Column('active',sa.Boolean(),nullable=False),sa.UniqueConstraint('id','organization_id'),sa.UniqueConstraint('tournament_id','venue_id'),sa.ForeignKeyConstraint(['tournament_id','organization_id'],['tournaments.id','tournaments.organization_id']),sa.ForeignKeyConstraint(['venue_id','organization_id'],['venues.id','venues.organization_id']))
    for table in ('rule_versions','tournament_venues'):
        op.create_index('ix_'+table+'_organization_id',table,['organization_id'])
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY organization_scope ON {table} USING (organization_id::text=current_setting('app.organization_id',true)) WITH CHECK (organization_id::text=current_setting('app.organization_id',true))")
    op.execute('CREATE TRIGGER immutable_rule_version BEFORE UPDATE OR DELETE ON rule_versions FOR EACH ROW EXECUTE FUNCTION prevent_journal_changes()')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='volleyball_app') THEN GRANT SELECT,INSERT ON rule_versions TO volleyball_app; GRANT SELECT,INSERT,UPDATE ON tournament_venues TO volleyball_app; END IF; END $$")
def downgrade():raise RuntimeError('Restore a reviewed backup rather than deleting rule history.')
