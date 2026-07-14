"""add site config and articles

Revision ID: g3h4i5j6k7l8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g3h4i5j6k7l8'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'site_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_name', sa.String(length=120), server_default='DNAISLAND', nullable=False),
        sa.Column('shutdown_enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('shutdown_message', sa.Text(), nullable=True),
        sa.Column('announcement_enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('announcement_content', sa.Text(), nullable=True),
        sa.Column('hero_enabled', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('hero_title', sa.Text(), nullable=True),
        sa.Column('hero_subtitle', sa.Text(), nullable=True),
        sa.Column('hero_buttons', sa.Text(), nullable=True),
        sa.Column('privacy_policy_url', sa.String(length=500), nullable=True),
        sa.Column('tos_url', sa.String(length=500), nullable=True),
        sa.Column('email_whitelist_enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('email_whitelist_suffixes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('cover', sa.Text(), nullable=True),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('is_published', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('show_author', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_articles_author_id', 'articles', ['author_id'])
    op.create_index('ix_articles_is_published', 'articles', ['is_published'])


def downgrade():
    op.drop_index('ix_articles_is_published', table_name='articles')
    op.drop_index('ix_articles_author_id', table_name='articles')
    op.drop_table('articles')
    op.drop_table('site_config')
