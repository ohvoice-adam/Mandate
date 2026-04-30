"""Add batch_events table and completed_at to batches

Revision ID: c1d2e3f4a5b6
Revises: 8804c9e85d28
Create Date: 2026-04-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = '8804c9e85d28'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('batches', sa.Column('completed_at', sa.DateTime(), nullable=True))
    # Backfill completed_at from created_at for existing complete batches
    op.execute("UPDATE batches SET completed_at = created_at WHERE status = 'complete'")

    op.create_table(
        'batch_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('performed_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('performed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('signatures_deleted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('note', sa.Text(), nullable=True),
    )
    op.create_index('ix_batch_events_batch_id', 'batch_events', ['batch_id'])


def downgrade():
    op.drop_index('ix_batch_events_batch_id', table_name='batch_events')
    op.drop_table('batch_events')
    op.drop_column('batches', 'completed_at')
