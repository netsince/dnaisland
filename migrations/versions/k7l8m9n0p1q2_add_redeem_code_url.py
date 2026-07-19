"""add redeem code url

Revision ID: k7l8m9n0p1q2
Revises: j6k7l8m9n0p1
Create Date: 2026-07-19 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'k7l8m9n0p1q2'
down_revision = 'e0707baf0182'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'site_config',
        sa.Column('redeem_code_url', sa.String(length=500), nullable=True),
    )


def downgrade():
    op.drop_column('site_config', 'redeem_code_url')
