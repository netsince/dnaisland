"""add ticket message image

Revision ID: i9j0k1l2m3n4
Revises: t1u2v3w4x5y6
Create Date: 2026-08-10 16:00:00.000000

- ticket_messages 增加 image_data 列：工单消息图片（WebP base64 data URL），可空。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles


@compiles(mysql.LONGTEXT, "sqlite")
def _compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"

# revision identifiers, used by Alembic.
revision = 'i9j0k1l2m3n4'
down_revision = 't1u2v3w4x5y6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ticket_messages', sa.Column('image_data', mysql.LONGTEXT(), nullable=True))


def downgrade():
    with op.batch_alter_table('ticket_messages', schema=None) as batch_op:
        batch_op.drop_column('image_data')