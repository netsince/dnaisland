"""add ticket tables

Revision ID: t1u2v3w4x5y6
Revises: m1n2o3p4q5r6
Create Date: 2026-08-10 14:00:00.000000

- ticket_categories:  工单类别（后台可增删改，用户提交时下拉选择）。
- tickets:            工单主体（类别/主题/内容/状态流转 open→replied→closed）。
- ticket_messages:    工单对话消息（用户 <-> 后台逐条往返）。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 't1u2v3w4x5y6'
down_revision = 'm1n2o3p4q5r6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ticket_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    op.create_table(
        'tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['category_id'], ['ticket_categories.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tickets_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_created_at'), ['created_at'], unique=False)

    op.create_table(
        'ticket_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('sender_role', sa.String(length=10), server_default='user', nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ticket_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ticket_messages_ticket_id'), ['ticket_id'], unique=False)

    # 预置常用工单类别
    op.execute(
        "INSERT INTO ticket_categories (name, sort_order, enabled) VALUES "
        "('账号问题', 1, 1), "
        "('充值 / BYOK', 2, 1), "
        "('举报与申诉', 3, 1), "
        "('功能建议', 4, 1), "
        "('其他', 5, 1)"
    )


def downgrade():
    with op.batch_alter_table('ticket_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ticket_messages_ticket_id'))
    op.drop_table('ticket_messages')

    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tickets_created_at'))
        batch_op.drop_index(batch_op.f('ix_tickets_status'))
        batch_op.drop_index(batch_op.f('ix_tickets_user_id'))
    op.drop_table('tickets')

    op.drop_table('ticket_categories')