"""world_cards 增加词条名预览（preview_names）

列表卡片要显示「前几个词条名」（内容型视觉锚点），但**不能在列表接口里解析
payload**：一页 20 个世界、每个几百 KB，解析一遍就是几十 MB 的 CPU。

所以在发布/编辑时算好一份快照存下来。

* 新增 preview_names（JSON 数组文本），默认 "[]"；
* **回填**已有数据：解析原文取前 8 个词条名。世界卡总量只有几百条，一次性
  回填可接受；解析失败的跳过（原文可能来自旧版本客户端）。

本迁移只加一列并回填，不改动、不删除任何既有列与数据。
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

# 卡片上最多显示这么多个词条名。
PREVIEW_LIMIT = 8


def _has_column(table: str, column: str) -> bool:
    """列是否已存在（幂等用）。离线模式无连接可查，一律当作不存在。"""
    if op.get_context().as_sql:
        return False
    inspector = inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def _names_of(payload):
    """从一段世界原文里取前若干条词条名；解析失败返回空。"""
    try:
        data = json.loads(payload or "{}")
    except (ValueError, TypeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
        if len(names) >= PREVIEW_LIMIT:
            break
    return names


def upgrade():
    if not _has_column("world_cards", "preview_names"):
        op.add_column(
            "world_cards", sa.Column("preview_names", sa.Text(), server_default="[]")
        )

    # 回填。离线（--sql）模式没有连接，跳过。
    if op.get_context().as_sql:
        return
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, payload FROM world_cards "
            "WHERE preview_names IS NULL OR preview_names = '[]'"
        )
    ).fetchall()
    for world_id, payload in rows:
        names = _names_of(payload)
        if not names:
            continue
        conn.execute(
            sa.text("UPDATE world_cards SET preview_names = :p WHERE id = :i"),
            {"p": json.dumps(names, ensure_ascii=False), "i": world_id},
        )


def downgrade():
    if _has_column("world_cards", "preview_names"):
        op.drop_column("world_cards", "preview_names")
