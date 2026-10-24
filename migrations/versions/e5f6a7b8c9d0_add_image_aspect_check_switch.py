"""给 site_config 增加「角色卡图片比例校验」开关

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "site_config",
        sa.Column(
            "image_aspect_check_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade():
    op.drop_column("site_config", "image_aspect_check_enabled")
