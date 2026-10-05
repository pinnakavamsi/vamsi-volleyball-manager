"""Require new manually provisioned organizers to replace temporary passwords."""
from alembic import op
import sqlalchemy as sa

revision = '20261005_manual_credentials'
down_revision = '20261005_relationship_guards'
branch_labels = None
depends_on = None


def upgrade():
    # Existing organizer passwords retain their current sign-in behavior.
    op.add_column('users', sa.Column('password_change_required', sa.Boolean(),
                                   server_default=sa.false(), nullable=False))
    # Existing invitations remain valid for in-app acceptance. Remove queued email
    # payloads now that the owner has selected manual organization onboarding.
    op.execute("""UPDATE identity_tokens
                  SET data = (data - 'delivery') || '{"delivery_status":"manual"}'::jsonb
                  WHERE kind = 'invitation' AND used_at IS NULL""")


def downgrade():
    op.drop_column('users', 'password_change_required')
