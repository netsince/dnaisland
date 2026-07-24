"""add comment likes and pinned status

Revision ID: 3d1bcff0dde7
Revises: l8m9n0p1q2r3
Create Date: 2026-07-24 15:13:25.307092

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3d1bcff0dde7'
down_revision = 'l8m9n0p1q2r3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'comment_likes',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'comment_id')
    )
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_pinned', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('reply_to_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_comments_is_pinned'), ['is_pinned'], unique=False)
        batch_op.create_foreign_key('fk_comments_reply_to_id_comments', 'comments', ['reply_to_id'], ['id'])


def downgrade():
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_comments_reply_to_id_comments', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_comments_is_pinned'))
        batch_op.drop_column('reply_to_id')
        batch_op.drop_column('is_pinned')

    op.drop_table('comment_likes')
