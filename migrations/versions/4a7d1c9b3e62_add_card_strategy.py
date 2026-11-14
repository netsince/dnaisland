"""给 cards 增加「对话策略」列 strategy

Revision ID: 4a7d1c9b3e62
Revises: e5f6a7b8c9d0
Create Date: 2026-11-15 03:17:51.996814

客户端角色卡新增了一组可选的提示词策略字段（对话策略），社区卡也要能携带。
整组以 JSON 字符串存一列：NULL = 未设置（客户端整个键都不发）。
仅新增 cards.strategy 一列，无其它 schema 变更。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4a7d1c9b3e62'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cards', sa.Column('strategy', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('cards', 'strategy')
