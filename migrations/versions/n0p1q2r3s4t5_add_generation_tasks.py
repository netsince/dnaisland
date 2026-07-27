"""add generation_tasks table for async image generation

Revision ID: n0p1q2r3s4t5
Revises: rename_comment_image_url_to_data
Create Date: 2026-07-27 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'n0p1q2r3s4t5'
down_revision = 'rename_comment_image_url_to_data'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'generation_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('model_id', sa.Integer(), nullable=True),
        sa.Column('model_name', sa.String(length=120), nullable=True),
        sa.Column('size', sa.String(length=20), nullable=True),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('references_count', sa.Integer(), nullable=False),
        sa.Column('reference_data', mysql.LONGTEXT(), nullable=True),
        sa.Column('result_log_id', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['model_id'], ['generation_models.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('generation_tasks', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_generation_tasks_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_generation_tasks_status'), ['status'], unique=False
        )


def downgrade():
    with op.batch_alter_table('generation_tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generation_tasks_status'))
        batch_op.drop_index(batch_op.f('ix_generation_tasks_user_id'))
    op.drop_table('generation_tasks')
