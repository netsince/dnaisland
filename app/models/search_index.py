"""检索倒排索引表（自带，替代不可用的数据库全文检索）。

两张表组成最小可用的倒排索引：

* [SearchGram]：倒排表 `(doc_type, doc_id, field, gram) -> tf`。
  `gram` 上是普通索引，查询时 `WHERE doc_type=? AND gram IN (...)` 取倒排链。
* [SearchDocStat]：文档统计 `(doc_type, doc_id) -> 各字段长度`，
  供 BM25 的字段长度归一化使用。

设计说明：
* `doc_type` 预留了扩展位（目前只有 `card`），将来要把茶馆帖子/用户纳入检索
  不需要再改表结构；
* 索引由 `app.services.search_service` 在卡片写入后自动维护，**只新增/更新自己的行**，
  不触碰任何业务数据；检索侧在索引缺失或为空时静默回退 LIKE。
"""

from ..extensions import db


class SearchGram(db.Model):
    """倒排表：一个 (文档, 字段, gram) 一行，tf 为该 gram 在该字段的出现次数。"""

    __tablename__ = "search_grams"

    doc_type = db.Column(db.String(16), primary_key=True)
    doc_id = db.Column(db.String(36), primary_key=True)
    field = db.Column(db.String(16), primary_key=True)
    # gram 最长 32 字符：CJK 二元组只有 2 个字符，拉丁整词可能更长。
    gram = db.Column(db.String(32), primary_key=True)
    tf = db.Column(db.Integer, nullable=False, server_default="1")

    __table_args__ = (
        # 查询主路径：按 gram 取倒排链。
        db.Index("ix_search_grams_gram", "gram"),
    )


class SearchDocStat(db.Model):
    """文档统计：各字段的字符数（BM25 长度归一化的分母）。"""

    __tablename__ = "search_doc_stats"

    doc_type = db.Column(db.String(16), primary_key=True)
    doc_id = db.Column(db.String(36), primary_key=True)
    # {"name": 4, "intro": 306, "persona": 5710, "tag": 12}
    field_lens = db.Column(db.JSON, nullable=False, default=dict)
    total_len = db.Column(db.Integer, nullable=False, server_default="0")
    indexed_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
