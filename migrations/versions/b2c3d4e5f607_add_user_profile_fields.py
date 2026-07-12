"""add user profile fields

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-07-13 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f607'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('avatar', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('bio', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('location', sa.String(length=80), nullable=True, server_default=''))
    op.add_column('users', sa.Column('website', sa.String(length=200), nullable=True, server_default=''))
    op.add_column('users', sa.Column('birthday', sa.Date(), nullable=True))


def downgrade():
    op.drop_column('users', 'birthday')
    op.drop_column('users', 'website')
    op.drop_column('users', 'location')
    op.drop_column('users', 'bio')
    op.drop_column('users', 'avatar')
