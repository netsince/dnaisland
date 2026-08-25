"""add proxy config and log tables

Revision ID: m1n2o3p4q5r6
Revises: d1e2f3a4b5c6
Create Date: 2026-08-08 10:00:00.000000

- proxy_configs: 用户级 BYOK 上游转发配置（一个账号一条）。
- proxy_logs:    转发审计日志（请求体 + 响应体成对落库）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles


@compiles(mysql.LONGTEXT, "sqlite")
def _compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"

# revision identifiers, used by Alembic.
revision = 'm1n2o3p4q5r6'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'proxy_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('upstream_base_url', sa.Text(), nullable=False),
        sa.Column('upstream_api_key', sa.Text(), nullable=False),
        sa.Column('token', sa.String(length=120), nullable=False),
        sa.Column('remark', sa.String(length=120), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('proxy_configs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proxy_configs_user_id'), ['user_id'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_proxy_configs_token'), ['token'], unique=True
        )

    op.create_table(
        'proxy_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('token', sa.String(length=120), nullable=True),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=True),
        sa.Column('upstream_url', sa.Text(), nullable=True),
        sa.Column('request_body', mysql.LONGTEXT(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', mysql.LONGTEXT(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['proxy_configs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('proxy_logs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proxy_logs_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_proxy_logs_config_id'), ['config_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_proxy_logs_created_at'), ['created_at'], unique=False
        )


def downgrade():
    with op.batch_alter_table('proxy_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proxy_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_proxy_logs_config_id'))
        batch_op.drop_index(batch_op.f('ix_proxy_logs_user_id'))

    op.drop_table('proxy_logs')

    with op.batch_alter_table('proxy_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proxy_configs_token'))
        batch_op.drop_index(batch_op.f('ix_proxy_configs_user_id'))

    op.drop_table('proxy_configs')
