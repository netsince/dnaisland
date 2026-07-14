"""add contact email

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-07-15 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i5j6k7l8m9n0'
down_revision = 'h4i5j6k7l8m9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'site_config',
        sa.Column('contact_email', sa.String(length=255), nullable=True),
    )


def downgrade():
    op.drop_column('site_config', 'contact_email')
