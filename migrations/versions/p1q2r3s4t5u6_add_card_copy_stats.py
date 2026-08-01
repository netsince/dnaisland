"""add card copy stats

Revision ID: p1q2r3s4t5u6
Revises: a0b1c2d3e4f5
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "p1q2r3s4t5u6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "card_copy_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.String(length=36), nullable=False),
        sa.Column("card_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=120), server_default="", nullable=False),
        sa.Column("copied_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("copier_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("card_copy_stats", schema=None) as batch_op:
        batch_op.create_index("ix_card_copy_stats_card_id", ["card_id"])
        batch_op.create_index("ix_card_copy_stats_user_id", ["user_id"])
        batch_op.create_index("ix_card_copy_stats_copied_at", ["copied_at"])


def downgrade():
    with op.batch_alter_table("card_copy_stats", schema=None) as batch_op:
        batch_op.drop_index("ix_card_copy_stats_copied_at")
        batch_op.drop_index("ix_card_copy_stats_user_id")
        batch_op.drop_index("ix_card_copy_stats_card_id")
    op.drop_table("card_copy_stats")
