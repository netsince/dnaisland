"""add reports table

Revision ID: a1b2c3d4e5f6
Revises: 333cb227113f
Create Date: 2026-07-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '333cb227113f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=20), nullable=False),
        sa.Column('target_id', sa.String(length=36), nullable=False),
        sa.Column('reason', sa.String(length=50), nullable=False),
        sa.Column('detail', sa.Text(), server_default='', nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('handled_at', sa.DateTime(), nullable=True),
        sa.Column('handled_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['handled_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reports_reporter_id'), ['reporter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_reports_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reports_status'))
        batch_op.drop_index(batch_op.f('ix_reports_reporter_id'))

    op.drop_table('reports')
