"""add teahouse posts

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-14 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a3b4c5d6e7'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'teahouse_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_hidden', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('moderated', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['teahouse_posts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_teahouse_posts_user_id', 'teahouse_posts', ['user_id'])
    op.create_index('ix_teahouse_posts_parent_id', 'teahouse_posts', ['parent_id'])
    op.create_index('ix_teahouse_posts_is_hidden', 'teahouse_posts', ['is_hidden'])
    op.create_index('ix_teahouse_posts_moderated', 'teahouse_posts', ['moderated'])

    op.create_table(
        'teahouse_post_likes',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['post_id'], ['teahouse_posts.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'post_id'),
    )


def downgrade():
    op.drop_table('teahouse_post_likes')
    op.drop_index('ix_teahouse_posts_moderated', table_name='teahouse_posts')
    op.drop_index('ix_teahouse_posts_is_hidden', table_name='teahouse_posts')
    op.drop_index('ix_teahouse_posts_parent_id', table_name='teahouse_posts')
    op.drop_index('ix_teahouse_posts_user_id', table_name='teahouse_posts')
    op.drop_table('teahouse_posts')
