"""add fulltext search indexes

为搜索高频文本字段在 MySQL 上创建 FULLTEXT 索引（ngram 解析器），
使中文子串/关键词搜索可走全文索引，替代全表 LIKE 扫描。
SQLite 不支持 FULLTEXT，迁移会安全跳过；应用层在 SQLite 上回退 LIKE。
"""
from alembic import op

revision = "b1f2f3f4f5f6"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def _is_mysql() -> bool:
    return op.get_bind().dialect.name == "mysql"


def upgrade():
    if not _is_mysql():
        return
    # 角色卡检索字段：名称 / 简介 / 人格设定
    op.create_index(
        "ix_cards_search_fts",
        "cards",
        ["name", "intro", "persona"],
        mysql_prefix="FULLTEXT",
        mysql_with={"parser": "ngram"},
    )
    # 茶馆帖子检索字段：正文
    op.create_index(
        "ix_teahouse_posts_content_fts",
        "teahouse_posts",
        ["content"],
        mysql_prefix="FULLTEXT",
        mysql_with={"parser": "ngram"},
    )


def downgrade():
    if not _is_mysql():
        return
    op.drop_index("ix_cards_search_fts", table_name="cards")
    op.drop_index("ix_teahouse_posts_content_fts", table_name="teahouse_posts")
