"""世界卡：Web 与 App 共用的查询与序列化。

存在的理由：列表查询原本在 routes/worlds.py 里被 API 和网页**各写了一遍**，
而且已经开始漂移 —— 网页那份漏了 tag 筛选。这类"两份一样的代码"每加一个筛选
条件就要改两处，漏一处就是两端行为不一致。

收拢成一层之后，规则只有一处：
* world_list_query() —— 唯一的列表查询构造（可见性 + 搜索 + 标签 + 排序）
* tags_map()         —— 批量取标签，避免列表 N+1
* can_view()         —— 单条可见性（详情/取原文的 404 判定）
* preview()          —— 详情页的词条名 + 一条示例
"""

import hashlib
import json
import uuid

from flask import current_app
from sqlalchemy import func, or_

from ..extensions import db
from ..models import WorldCard, WorldTag

# 详情页预览最多给这么多词条名（页面上放不下更多，也没必要）。
MAX_ENTRY_NAMES = 60

# 排序口径。
SORT_NEW = "new"
SORT_COPIES = "copies"


def world_list_query(viewer, *, keyword="", tag="", sort=SORT_NEW):
    """构造世界卡列表查询。**web 与 api 必须共用这一个函数。**

    * keyword —— 搜名称 / 简介（LIKE 就够：世界卡只有几百条）
    * tag —— 按标签精确筛选
    * sort —— new（提交时间倒序）| copies（复制量倒序）

    复制量是这个栏目最有价值的排序信号 —— 比点赞可信：一个人愿意把世界导入
    自己的应用，说明它真的有用。
    """
    query = WorldCard.visible_to(viewer)

    keyword = (keyword or "").strip()
    if keyword:
        # 优先走自带倒排（世界卡索引里**包含词条名与触发词**，所以「按词条名找
        # 世界书」能搜到）。索引不可用、或这次查询切不出 token 时回退 LIKE ——
        # 与角色卡搜索同一套兜底策略，保证永远不会 500、也不会莫名空结果。
        from . import search_service

        ranked = (
            search_service.rank_world_ids(keyword)
            if search_service.index_available(doc_type=search_service.DOC_WORLD)
            else []
        )
        if ranked:
            query = query.filter(WorldCard.id.in_(ranked))
        else:
            like = "%" + keyword + "%"
            query = query.filter(
                or_(WorldCard.name.like(like), WorldCard.summary.like(like))
            )

    tag = (tag or "").strip()
    if tag:
        query = query.filter(
            WorldCard.id.in_(
                db.session.query(WorldTag.world_id).filter(WorldTag.tag == tag)
            )
        )

    if (sort or SORT_NEW).strip() == SORT_COPIES:
        return query.order_by(
            WorldCard.copy_count.desc(), WorldCard.created_at.desc()
        )
    return query.order_by(WorldCard.created_at.desc())


def user_worlds_query(viewer, author_id):
    """某个作者发布的世界卡（列表用）。

    走 WorldCard.visible_to —— 作者本人能看到自己的待审核条目，别人只看
    已通过的。与角色卡「作者主页」同一口径，不另写一套可见性。
    """
    return (
        WorldCard.visible_to(viewer)
        .filter(WorldCard.author_id == author_id)
        .order_by(WorldCard.created_at.desc())
    )


def popular_tags(viewer=None, limit=20):
    """世界卡热门标签（含计数），按使用次数降序。

    世界卡总量只有几百条，直接 group by 就够；不套 TTL 缓存 —— 角色卡那边
    加缓存是因为全站卡片数量级不同，这里多一层缓存只会让「刚发布的标签」
    晚五分钟出现。

    计数只统计对 viewer 可见的世界卡，与 popular_tags(card) 同一取舍：不该
    让「看不到的卡」的标签出现在筛选条里。
    """
    visible_ids = WorldCard.visible_to(viewer).with_entities(WorldCard.id)
    rows = (
        db.session.query(WorldTag.tag, func.count(WorldTag.world_id).label("n"))
        .filter(WorldTag.world_id.in_(visible_ids))
        .group_by(WorldTag.tag)
        .order_by(func.count(WorldTag.world_id).desc(), WorldTag.tag.asc())
        .limit(limit)
        .all()
    )
    return [{"tag": row.tag, "count": row.n} for row in rows]


def tags_map(world_ids):
    """批量取标签，避免列表里 N+1。"""
    ids = list(world_ids or [])
    if not ids:
        return {}
    out = {}
    for row in WorldTag.query.filter(WorldTag.world_id.in_(ids)).all():
        out.setdefault(row.world_id, []).append(row.tag)
    return out


def favorite_state(world_id, viewer):
    """返回 (我是否已收藏, 总收藏数)。未登录时 favorited 恒为 False。"""
    from ..models import WorldFavorite

    count = WorldFavorite.query.filter_by(world_id=world_id).count()
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return False, count
    hit = WorldFavorite.query.filter_by(
        user_id=viewer.id, world_id=world_id
    ).first()
    return hit is not None, count


def entry_index(world, limit=500):
    """词条索引：给详情页做「分组 / 折叠 / 搜索」用。

    只读 entries[].name / type / keys —— **不碰其余字段**，也不校验结构。
    词条正文（description）**不在索引里**：那是"抄走"的关键内容，详情页只给
    一条示例，其余只给名字，这是刻意的取舍（全展示没人愿发布，全不展示没人
    愿导入）。

    [limit] 用来给超大的世界书兜底，避免一页渲染几千行。
    """
    try:
        data = json.loads(world.payload or "{}")
    except (ValueError, TypeError):
        return []
    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        keys = entry.get("keys")
        out.append(
            {
                "name": name.strip(),
                "type": "person" if entry.get("type") == "person" else "noun",
                "keys": (
                    [k for k in keys if isinstance(k, str)][:8]
                    if isinstance(keys, list)
                    else []
                ),
            }
        )
        if limit and len(out) >= limit:
            break
    return out


def can_view(world, viewer):
    """单条可见性判定（详情 / 取原文 / 下载的 404 判定）。与 WorldCard.visible_to 同口径。"""
    if world is None:
        return False
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        if getattr(viewer, "is_super_admin", False):
            return True
        if world.author_id == viewer.id:
            return not world.is_hidden
    return world.is_public


def preview(world):
    """从原文里读词条名 + 第一条示例。

    解析失败一律降级成空 —— 原文可能来自旧版本客户端，不该让详情页 500。
    只读 entries[].name 与 entries[0]，**不校验词条结构**（词条字段还会继续长）。
    """
    try:
        data = json.loads(world.payload or "{}")
    except (ValueError, TypeError):
        return [], None
    entries = data.get("entries")
    if not isinstance(entries, list):
        return [], None
    names = []
    for entry in entries[:MAX_ENTRY_NAMES]:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    sample = entries[0] if entries and isinstance(entries[0], dict) else None
    return names, sample

def publish_world(author, raw):
    """用一段世界 JSON 原文创建世界卡。**Web 与 App 共用这一个函数。**

    返回 (world, error, reused)：
    * 出错 —— (None, 可读错误, False)
    * 幂等复用已有的待审核记录 —— (world, None, True)
    * 新建 —— (world, None, False)

    原文按收到的字节**原样入库**：不做 json.dumps(json.loads(...)) 往返，
    那会改掉键序、空格和数字格式。服务端只读顶层字段填列表用的元信息。
    """
    from ..models import WorldCard, WorldTag

    raw = raw or ""
    if not raw.strip():
        return None, "世界内容为空", False
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "世界内容不是合法的 JSON", False
    if not isinstance(data, dict):
        return None, "世界内容不是合法的 JSON 对象", False

    name = data.get("name") if isinstance(data.get("name"), str) else ""
    name = name.strip()[:120]
    if not name:
        return None, "世界需要一个名称", False

    entries = data.get("entries")
    entry_count = len(entries) if isinstance(entries, list) else 0
    summary = data.get("summary") if isinstance(data.get("summary"), str) else ""
    raw_tags = data.get("tags")
    tags = (
        [str(t)[:50] for t in raw_tags if isinstance(t, (str, int, float))][:20]
        if isinstance(raw_tags, list)
        else []
    )

    payload_bytes = len(raw.encode("utf-8"))
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # 同一作者重复提交同样的世界：幂等复用那条待审核记录，不新建。
    existing = WorldCard.query.filter(
        WorldCard.author_id == author.id,
        WorldCard.content_hash == content_hash,
        WorldCard.status == "pending",
    ).first()
    if existing is not None:
        return existing, None, True

    world = WorldCard(
        id=str(uuid.uuid4()),
        author_id=author.id,
        name=name,
        summary=summary,
        entry_count=entry_count,
        payload_bytes=payload_bytes,
        payload=raw,
        content_hash=content_hash,
        status="pending",
    )
    db.session.add(world)
    db.session.flush()
    for tag in dict.fromkeys(tags):
        db.session.add(WorldTag(world_id=world.id, tag=tag))
    db.session.commit()

    # 建倒排索引（含词条名与触发词）。**索引失败绝不能影响发布** ——
    # 搜索退化到 LIKE 是可接受的，发布失败不可接受。
    try:
        from .search_service import reindex_world

        reindex_world(world.id)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.warning("世界卡建索引失败（不影响发布）: %s", exc)

    return world, None, False
