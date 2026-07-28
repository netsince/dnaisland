"""add card_image optimized flag

Revision ID: a0b1c2d3e4f5
Revises: n0p1q2r3s4t5
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a0b1c2d3e4f5"
down_revision = "n0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("card_images", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "optimized",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.create_index("ix_card_images_optimized", ["optimized"])


def downgrade():
    with op.batch_alter_table("card_images", schema=None) as batch_op:
        batch_op.drop_index("ix_card_images_optimized")
        batch_op.drop_column("optimized")
