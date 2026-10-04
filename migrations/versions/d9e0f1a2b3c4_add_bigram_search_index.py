"""add bigram search index tables

新增自带检索倒排表（`search_grams` / `search_doc_stats`），替代对中文无效的数据库全文检索。

背景（生产实测）：生产是 **MariaDB**，没有 ngram 解析器（`ngram_token_size` 变量不存在），
`b1f2f3f4f5f6` 里的 `WITH PARSER ngram` 分支从未生效，落回默认解析器 —— 一整段中文被当成
一个 token，且 `innodb_ft_min_token_size=3` 丢弃短词。实测卡名「后藤一里（Gotou Hitori）/
波奇（ぼっち）」搜「藤」「藤一」「后藤一」全部 0 命中，搜「波奇」也是 0，只有整段命中；
417 张中文名卡里 381 张卡名无标点，等于"必须一字不差输完整卡名"。

于是检索改为「CJK bigram 倒排 + BM25F」自建索引（见 `app/services/search_service.py`）。

本迁移只**新增两张表**，不改动、不删除任何既有表/列/数据：
* `search_grams`：倒排表 (doc_type, doc_id, field, gram) -> tf，gram 上建普通索引；
* `search_doc_stats`：文档统计 (doc_type, doc_id) -> 各字段字符数（BM25 长度归一化用）。

幂等：已存在则跳过（便于在已手工建过表的环境重复执行）。
索引内容由 `flask search-reindex` 回填，日常由 SQLAlchemy 事件自动维护。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "d9e0f1a2b3c4"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    """表是否已存在（用于幂等）。

    离线模式（`flask db upgrade --sql`）没有真实连接可 inspect，此时一律当作
    「不存在」，让生成的 SQL 里包含 CREATE TABLE（离线脚本本来就是拿来审阅的）。
    """
    if op.get_context().as_sql:
        return False
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("search_grams"):
        op.create_table(
            "search_grams",
            sa.Column("doc_type", sa.String(length=16), nullable=False),
            sa.Column("doc_id", sa.String(length=36), nullable=False),
            sa.Column("field", sa.String(length=16), nullable=False),
            sa.Column("gram", sa.String(length=32), nullable=False),
            sa.Column("tf", sa.Integer(), server_default="1", nullable=False),
            sa.PrimaryKeyConstraint("doc_type", "doc_id", "field", "gram"),
        )
        op.create_index("ix_search_grams_gram", "search_grams", ["gram"])

    if not _has_table("search_doc_stats"):
        op.create_table(
            "search_doc_stats",
            sa.Column("doc_type", sa.String(length=16), nullable=False),
            sa.Column("doc_id", sa.String(length=36), nullable=False),
            sa.Column("field_lens", sa.JSON(), nullable=False),
            sa.Column("total_len", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "indexed_at",
                sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("doc_type", "doc_id"),
        )


def downgrade():
    # 回滚只删本迁移新增的两张表（不触碰任何既有数据）。
    if _has_table("search_grams"):
        op.drop_index("ix_search_grams_gram", table_name="search_grams")
        op.drop_table("search_grams")
    if _has_table("search_doc_stats"):
        op.drop_table("search_doc_stats")
