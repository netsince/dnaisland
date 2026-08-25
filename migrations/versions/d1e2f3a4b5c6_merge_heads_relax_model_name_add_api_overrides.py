"""merge migration heads; relax generation_models.name; add per-model api overrides

Revision ID: d1e2f3a4b5c6
Revises: c6d7e8f9a1b2, c7d8e9f0a1b2
Create Date: 2026-08-08 00:00:00.000000

- 合并历史迁移分叉（c6d7e8f9a1b2 赞助/内容指纹 与 c7d8e9f0a1b2 作者注释），
  消除 multiple heads，后续 `flask db upgrade` 才能继续执行。
- generation_models.name 去掉唯一约束（允许同一调用名配置多条，如活动免费版），
  保留普通索引用于查询。
- generation_models 新增 api_base_url / api_key（模型级 API 配置，为空回退全局）。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = ('c6d7e8f9a1b2', 'c7d8e9f0a1b2')
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('generation_models', schema=None) as batch_op:
        batch_op.drop_constraint('name', type_='unique')
        batch_op.create_index(
            batch_op.f('ix_generation_models_name'), ['name'], unique=False
        )
        batch_op.add_column(sa.Column('api_base_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('api_key', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('generation_models', schema=None) as batch_op:
        batch_op.drop_column('api_key')
        batch_op.drop_column('api_base_url')
        batch_op.drop_index(batch_op.f('ix_generation_models_name'))
        batch_op.create_unique_constraint('name', ['name'])