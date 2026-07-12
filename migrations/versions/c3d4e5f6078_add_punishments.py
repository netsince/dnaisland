"""add punishments table and migrate legacy bans

Revision ID: c3d4e5f6078
Revises: b2c3d4e5f607
Create Date: 2026-07-13 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6078'
down_revision = 'b2c3d4e5f607'
branch_labels = None
depends_on = None

# 迁移历史封禁用户时为其施加的完整处罚集合
LEGACY_PUNISHMENTS = (
    "mute",
    "profile_banned",
    "reset_profile",
    "no_edit_profile",
    "hide_comments",
    "hide_cards",
    "clear_avatar",
)


def upgrade():
    op.create_table(
        "punishments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("handled_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("appealed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("appeal_reason", sa.Text(), nullable=True),
        sa.Column("appeal_status", sa.String(length=20), server_default="none", nullable=False),
        sa.Column("appeal_at", sa.DateTime(), nullable=True),
        sa.Column("appeal_handled_at", sa.DateTime(), nullable=True),
        sa.Column("appeal_handled_by", sa.Integer(), nullable=True),
        sa.Column("appeal_reply", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["handled_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["appeal_handled_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_punishments_user_id", "punishments", ["user_id"])
    op.create_index("ix_punishments_type", "punishments", ["type"])

    # 将历史上 role='banned' 的用户转换为完整处罚集合，并重置其资料
    conn = op.get_bind()
    banned = conn.execute(sa.text("SELECT id FROM users WHERE role = 'banned'")).fetchall()
    now = datetime.utcnow()
    for (uid,) in banned:
        for ptype in LEGACY_PUNISHMENTS:
            conn.execute(
                sa.text(
                    "INSERT INTO punishments "
                    "(user_id, type, reason, created_at, status, appealed, appeal_status) "
                    "VALUES (:uid, :t, '历史封禁迁移为处罚', :now, 'active', 0, 'none')"
                ),
                {"uid": uid, "t": ptype, "now": now},
            )
    if banned:
        conn.execute(
            sa.text(
                "UPDATE users SET "
                "nickname = CONCAT('UID', id), bio = NULL, location = NULL, "
                "website = NULL, birthday = NULL, avatar = NULL, role = 'user' "
                "WHERE role = 'banned'"
            )
        )


def downgrade():
    op.drop_index("ix_punishments_type", table_name="punishments")
    op.drop_index("ix_punishments_user_id", table_name="punishments")
    op.drop_table("punishments")
