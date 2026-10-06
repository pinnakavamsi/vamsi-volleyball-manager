"""Organization support cases with explicit platform support access."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261006_organization_support'
down_revision = '20261005_manual_credentials'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'support_cases',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('opened_by', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('subject', sa.String(180), nullable=False),
        sa.Column('category', sa.String(40), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(30), server_default='open', nullable=False),
        sa.UniqueConstraint('id', 'organization_id'),
        sa.CheckConstraint("status IN ('open','in_progress','waiting_on_organization','resolved','closed')", name='ck_support_cases_status'),
    )
    op.create_index('ix_support_cases_organization_id', 'support_cases', ['organization_id'])
    op.create_index('ix_support_case_scope_status_time', 'support_cases', ['organization_id', 'status', 'created_at'])
    op.create_table(
        'support_messages',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('case_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('author_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('author_type', sa.String(20), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.UniqueConstraint('id', 'organization_id'),
        sa.ForeignKeyConstraint(['case_id', 'organization_id'], ['support_cases.id', 'support_cases.organization_id'], ondelete='RESTRICT'),
        sa.CheckConstraint("author_type IN ('organization','platform_admin')", name='ck_support_messages_author_type'),
    )
    op.create_index('ix_support_messages_organization_id', 'support_messages', ['organization_id'])
    op.create_index('ix_support_message_case_time', 'support_messages', ['case_id', 'created_at'])
    for table in ('support_cases', 'support_messages'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY organization_or_platform_support ON {table} USING (organization_id::text = current_setting('app.organization_id', true) OR current_setting('app.platform_admin', true) = 'true') WITH CHECK (organization_id::text = current_setting('app.organization_id', true) OR current_setting('app.platform_admin', true) = 'true')")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='volleyball_app') THEN
       GRANT SELECT,INSERT,UPDATE,DELETE ON support_cases,support_messages TO volleyball_app;
       END IF; END $$""")


def downgrade():
    raise RuntimeError('Support case data is customer correspondence; use a reviewed restore, not destructive downgrade.')
