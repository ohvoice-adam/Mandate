"""Add last_seen to users

Revision ID: a2b3c4d5e6f7
Revises: f3a8b1c2d4e5
Create Date: 2026-03-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'f3a8b1c2d4e5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('last_seen', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('users', 'last_seen')
