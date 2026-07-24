"""rename comment image_url to image_data (store as data URL in DB)

评论图片由落地目录改为以 base64 data URL 存入数据库，字段名与类型一并调整。

Revision ID: rename_comment_image_url_to_data
Revises: a1b2c3d4e5f8
Create Date: 2026-07-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = 'rename_comment_image_url_to_data'
down_revision = 'a1b2c3d4e5f8'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = [r[0] for r in bind.execute(text("SHOW COLUMNS FROM comments")).fetchall()]
    with op.batch_alter_table('comments', schema=None) as batch_op:
        if 'image_url' in cols and 'image_data' in cols:
            # 异常重试导致两列并存：先把旧 image_url 数据并入 image_data，再删除旧列
            bind.execute(
                text(
                    "UPDATE comments SET image_data = image_url "
                    "WHERE image_data IS NULL AND image_url IS NOT NULL"
                )
            )
            batch_op.drop_column('image_url')
        elif 'image_url' in cols:
            # 正常情况：旧值（如 "uploads/comments/202401/xxx.webp"）均 < 255 字符，
            # 可无损转入 Text；同时把列名改为 image_data 以反映“存库 data URL”的语义。
            batch_op.alter_column(
                'image_url',
                new_column_name='image_data',
                type_=sa.Text(),
                existing_type=sa.String(length=255),
                existing_nullable=True,
            )


def downgrade():
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.alter_column(
            'image_data',
            new_column_name='image_url',
            type_=sa.String(length=255),
            existing_type=sa.Text(),
            existing_nullable=True,
        )
