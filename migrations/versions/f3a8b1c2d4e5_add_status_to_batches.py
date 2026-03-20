"""Add status to batches

Revision ID: f3a8b1c2d4e5
Revises: 9d581127453d
Create Date: 2026-03-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a8b1c2d4e5'
down_revision = '9d581127453d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('batches', sa.Column('status', sa.String(length=20), nullable=False, server_default='complete'))
    # Set existing batches to 'complete' since they predate this feature
    op.execute("UPDATE batches SET status = 'complete'")
    # Remove the server default now that backfill is done; app sets the default
    op.alter_column('batches', 'status', server_default=None)


def downgrade():
    op.drop_column('batches', 'status')
