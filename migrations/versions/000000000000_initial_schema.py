"""Initial schema

Revision ID: 000000000000
Revises:
Create Date: 2026-02-10 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '000000000000'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_settings_key', 'settings', ['key'], unique=False)

    # organizations.organization column — renamed to `name` by migration 4de6f87c264e
    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'data_enterers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # collectors.organization_id added by migration 4de6f87c264e
    op.create_table(
        'collectors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'paid_collectors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('collector_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['collector_id'], ['collectors.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    # users: role added by 7b75052ca987, organization_id by 4de6f87c264e,
    #        must_change_password by a1b2c3d4e5f6, last_seen by a2b3c4d5e6f7
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=True),
        sa.Column('first_name', sa.String(length=100), nullable=True),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.create_table(
        'books',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('book_number', sa.String(length=50), nullable=False),
        sa.Column('collector_id', sa.Integer(), nullable=True),
        sa.Column('date_out', sa.Date(), nullable=True),
        sa.Column('date_back', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['collector_id'], ['collectors.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_books_book_number', 'books', ['book_number'], unique=False)

    # batches.status added by migration f3a8b1c2d4e5
    op.create_table(
        'batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('book_number', sa.String(length=50), nullable=True),
        sa.Column('collector_id', sa.Integer(), nullable=True),
        sa.Column('enterer_id', sa.Integer(), nullable=True),
        sa.Column('enterer_first', sa.String(length=100), nullable=True),
        sa.Column('enterer_last', sa.String(length=100), nullable=True),
        sa.Column('enterer_email', sa.String(length=120), nullable=True),
        sa.Column('date_entered', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['book_id'], ['books.id']),
        sa.ForeignKeyConstraint(['collector_id'], ['collectors.id']),
        sa.ForeignKeyConstraint(['enterer_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'signatures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sos_voterid', sa.String(length=20), nullable=True),
        sa.Column('county_number', sa.String(length=10), nullable=True),
        sa.Column('book_id', sa.Integer(), nullable=True),
        sa.Column('batch_id', sa.Integer(), nullable=True),
        sa.Column('residential_address1', sa.String(length=255), nullable=True),
        sa.Column('residential_address2', sa.String(length=100), nullable=True),
        sa.Column('residential_city', sa.String(length=100), nullable=True),
        sa.Column('residential_state', sa.String(length=2), nullable=True),
        sa.Column('residential_zip', sa.String(length=10), nullable=True),
        sa.Column('registered_city', sa.String(length=100), nullable=True),
        sa.Column('matched', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id']),
        sa.ForeignKeyConstraint(['book_id'], ['books.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_signatures_sos_voterid', 'signatures', ['sos_voterid'], unique=False)

    # voters: idx_voters_address_btree added by 7b75052ca987
    #         GIN trgm indexes are created at app startup, not via migrations
    op.create_table(
        'voters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sos_voterid', sa.String(length=20), nullable=True),
        sa.Column('county_number', sa.String(length=10), nullable=True),
        sa.Column('first_name', sa.String(length=100), nullable=True),
        sa.Column('middle_name', sa.String(length=100), nullable=True),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('residential_address1', sa.String(length=255), nullable=True),
        sa.Column('residential_address2', sa.String(length=100), nullable=True),
        sa.Column('residential_city', sa.String(length=100), nullable=True),
        sa.Column('residential_state', sa.String(length=2), nullable=True),
        sa.Column('residential_zip', sa.String(length=10), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('registration_date', sa.Date(), nullable=True),
        sa.Column('precinct_code', sa.String(length=50), nullable=True),
        sa.Column('precinct_name', sa.String(length=200), nullable=True),
        sa.Column('ward', sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_voters_sos_voterid', 'voters', ['sos_voterid'], unique=False)
    op.create_index('ix_voters_county_number', 'voters', ['county_number'], unique=False)

    # voter_imports: detected_county_ids added by 2c80b65c1cf1
    op.create_table(
        'voter_imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('county_name', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('total_rows', sa.Integer(), nullable=True),
        sa.Column('processed_rows', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('rollback_voter_id', sa.Integer(), nullable=True),
        sa.Column('backup_table', sa.String(length=100), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('voter_imports')
    op.drop_index('ix_voters_county_number', table_name='voters')
    op.drop_index('ix_voters_sos_voterid', table_name='voters')
    op.drop_table('voters')
    op.drop_index('ix_signatures_sos_voterid', table_name='signatures')
    op.drop_table('signatures')
    op.drop_table('batches')
    op.drop_index('ix_books_book_number', table_name='books')
    op.drop_table('books')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
    op.drop_table('paid_collectors')
    op.drop_table('collectors')
    op.drop_table('data_enterers')
    op.drop_table('organizations')
    op.drop_index('ix_settings_key', table_name='settings')
    op.drop_table('settings')
