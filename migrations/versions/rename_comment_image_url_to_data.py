"""rename comment image_url to image_data (store as data URL in DB)

评论图片由落地目录改为以 base64 data URL 存入数据库，字段名与类型一并调整。

Revision ID: rename_comment_image_url_to_data
Revises: 2eea28dec912
Create Date: 2026-07-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'rename_comment_image_url_to_data'
down_revision = '2eea28dec912'
branch_labels = None
depends_on = None


def upgrade():
    # 旧值（如 "uploads/comments/202401/xxx.webp"）均 < 255 字符，可无损转入 Text；
    # 同时把列名改为 image_data 以反映“存库 data URL”的语义。
    with op.batch_alter_table('comments', schema=None) as batch_op:
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
