"""add user_login_events table

Revision ID: 44702ab78e2e
Revises: a2b3c4d5e6f7
Create Date: 2026-03-28 11:30:13.923470

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '44702ab78e2e'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_login_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('logged_in_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_login_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_login_events_logged_in_at'), ['logged_in_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_login_events_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('user_login_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_login_events_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_login_events_logged_in_at'))

    op.drop_table('user_login_events')
