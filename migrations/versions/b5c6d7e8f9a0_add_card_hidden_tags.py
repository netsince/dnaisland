"""add cards.hidden_tags and cards.boost_factor

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-10-01 18:00:00.000000

新增两个字段，用于「隐匿标签」（仅超级管理员可见/可设置）：
- cards.hidden_tags : JSON 数组，存 app.services.card_hidden_tags 注册表里的稳定 key；
- cards.boost_factor: 由标签派生的热度分乘数（1.0 = 不降权），供 SQL 排序直接相乘。

两者由 card_hidden_tags.set_hidden_tags 唯一写入、始终同步。现有卡片回填
hidden_tags='[]' / boost_factor=1.0，即默认不降权。

回滚说明：downgrade 直接删除两列，已设置的隐匿标签随之丢失；不影响卡片本体数据。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b5c6d7e8f9a0'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('hidden_tags', sa.JSON(), nullable=False, server_default='[]')
        )
        batch_op.add_column(
            sa.Column('boost_factor', sa.Float(), nullable=False, server_default='1.0')
        )


def downgrade():
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.drop_column('boost_factor')
        batch_op.drop_column('hidden_tags')
