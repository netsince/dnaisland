"""add user platform verification

Revision ID: d4e5f6078a1
Revises: c3d4e5f6078
Create Date: 2026-07-13 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6078a1'
down_revision = 'c3d4e5f6078'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("verified", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("verified_label", sa.String(length=50), nullable=True),
    )
    op.create_index("ix_users_verified", "users", ["verified"])


def downgrade():
    op.drop_index("ix_users_verified", table_name="users")
    op.drop_column("users", "verified_label")
    op.drop_column("users", "verified")
