"""add card cover_focus

Revision ID: l8m9n0p1q2r3
Revises: 826e1c045b2b
Create Date: 2026-07-22 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'l8m9n0p1q2r3'
down_revision = '826e1c045b2b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cover_focus', sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.drop_column('cover_focus')
