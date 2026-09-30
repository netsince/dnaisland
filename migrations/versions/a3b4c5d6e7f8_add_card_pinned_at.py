"""add cards.pinned_at for author-pinned cards

Revision ID: a3b4c5d6e7f8
Revises: d8e9f0a1b2c3
Create Date: 2026-10-01 16:00:00.000000

新增 cards.pinned_at：作者可把最多 2 张「已通过」的角色卡置顶到个人主页最前
（规则见 app/services/card_edit_service.py 的 set_card_pinned / MAX_PINNED_CARDS）。
存时间戳而非布尔值，同时用于排序（见 app/routes/card_lists.py 的 _PINNED_FIRST）。

回滚说明：downgrade 直接删除该列，置顶关系随之丢失；没有任何其它表引用它，
不影响卡片本体与其它数据。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a3b4c5d6e7f8'
down_revision = 'd8e9f0a1b2c3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pinned_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_cards_pinned_at', ['pinned_at'])


def downgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.drop_index('ix_cards_pinned_at')
        batch_op.drop_column('pinned_at')
