"""add fulltext search indexes

为搜索高频文本字段在 MySQL/MariaDB 上创建 FULLTEXT 索引，使中文关键词搜索可走全文索引，
替代全表 LIKE 扫描。SQLite 不支持 FULLTEXT，迁移会安全跳过；应用层在 SQLite 上回退 LIKE。

注意：ngram 解析器是 MySQL 专有插件，MariaDB 不支持（报 Function 'ngram' is not defined）。
故在此按数据库类型区分：MySQL 用 ngram（中文分词更佳），MariaDB 回退为默认解析器的普通
FULLTEXT 索引，保证迁移在两种环境下都能通过。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

revision = "b1f2f3f4f5f6"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def _is_mysql() -> bool:
    return op.get_bind().dialect.name == "mysql"


def _is_mariadb() -> bool:
    try:
        version = op.get_bind().scalar(sa.text("select version()"))
        return bool(version) and "MariaDB" in str(version)
    except Exception:
        return False


# 需要全文索引的检索字段：(索引名, 表名, 列)
_FTS_INDEXES = (
    ("ix_cards_search_fts", "cards", "(name, intro, persona)"),
    ("ix_teahouse_posts_content_fts", "teahouse_posts", "(content)"),
)


def _create_fts(index_name, table, columns):
    op.execute(f"CREATE FULLTEXT INDEX {index_name} ON {table} {columns}")


def upgrade():
    if not _is_mysql():
        return
    if _is_mariadb():
        # MariaDB 不支持 ngram，回退为默认解析器
        for index_name, table, columns in _FTS_INDEXES:
            _create_fts(index_name, table, columns)
        return
    # MySQL：优先 ngram（中文分词更佳）
    for index_name, table, columns in _FTS_INDEXES:
        try:
            op.execute(
                f"CREATE FULLTEXT INDEX {index_name} ON {table} {columns} WITH PARSER ngram"
            )
        except OperationalError:
            # 极少数 MySQL 编译环境缺 ngram 插件时降级为默认解析器，避免迁移中断
            _create_fts(index_name, table, columns)


def downgrade():
    if not _is_mysql():
        return
    for index_name, table, _columns in _FTS_INDEXES:
        op.execute(f"DROP INDEX {index_name} ON {table}")
