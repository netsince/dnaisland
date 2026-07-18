"""add memorial banner url

Revision ID: j6k7l8m9n0p1
Revises: i5j6k7l8m9n0
Create Date: 2026-07-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'j6k7l8m9n0p1'
down_revision = 'i5j6k7l8m9n0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'site_config',
        sa.Column('memorial_banner_url', sa.String(length=500), nullable=True),
    )


def downgrade():
    op.drop_column('site_config', 'memorial_banner_url')
