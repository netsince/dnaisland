"""add comment moderation fields

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6078a1
Create Date: 2026-07-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'd4e5f6078a1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_hidden', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('moderated', sa.Boolean(), server_default='0', nullable=False))
    # 历史评论视为已审核，避免全部涌入新的「先发后审」队列
    op.execute("UPDATE comments SET moderated = 1 WHERE moderated = 0")


def downgrade():
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_column('moderated')
        batch_op.drop_column('is_hidden')
