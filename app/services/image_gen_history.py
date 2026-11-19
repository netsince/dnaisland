"""生图历史的读取：可见性判定 + 分页 + 序列化，**唯一入口**。

网页端 `/image-gen/api/logs` 与 App 端 `/api/v1/image-gen/logs` 是同一份历史的两个出口，
必须走同一份逻辑 —— 同一个坑分叉成两处，就是改了一处漏一处的开始。

不变量（旧写法全违反了，代价是用户的记录页整页空白）：

1. 可见性只由 [paginate_logs] 里的一处 SQL 条件决定，判定不在 Python 的循环里做。
   旧写法先 `paginate()` 取一页原始记录、再逐条 `continue` 掉没有产出图的记录：当最新的
   12 条恰好全是失败记录时，`items` 是空的，而 `total`/`pages` 仍按全部记录算
   （事故现场：一位社区用户 495 条记录里最新 23 条连续失败，记录页显示「还没有生图记录」）。
2. 分页信息与 items 出自同一条查询，三者不可能互相矛盾。
3. 排序带 id 兜底：created_at 只到秒，同一秒批量插入的记录翻页时会漂移。

判定「有没有产出图」只看 `count`，不读 `images`：`images` 是 LONGTEXT，翻一页就要为几 MB
base64 解一次码，而判定需要的只是一个数字。
"""

from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import defer

from ..constants import points_to_str
from ..models import GenerationLog

# `?status=` 允许的三种状态；其余取值一律当作没传。
STATUS_FILTERS = ("success", "partial", "failed")


def parse_flag(raw):
    """把 query string 里的布尔开关解析成 bool（`1` / `true` / `yes` 为真）。"""
    return (raw or "").strip().lower() in ("1", "true", "yes")


def has_output_clause():
    """「这次生成有产出图」的 SQL 条件。

    失败记录必然没有产出图；其余状态按请求张数判断（`count` 为空按 1 张算，与历史上
    `count or 1` 的语义一致）。
    """
    return and_(
        GenerationLog.status != "failed",
        or_(GenerationLog.count.is_(None), GenerationLog.count > 0),
    )


def paginate_logs(user_id, *, page, per_page, status="", include_failed=False):
    """取 [user_id] 的一页生图记录，**过滤在 SQL 里完成**。

    - `status` 命中 [STATUS_FILTERS] 时只返回该状态（「失败记录」页靠它翻页）；
    - 否则 `include_failed` 为真时返回全部记录（App 的详情/历史要显示失败原因）；
    - 否则只返回有产出图的记录（默认：瀑布流只认图片）。
    """
    query = GenerationLog.query.options(
        defer(GenerationLog.images),
        defer(GenerationLog.reference_images),
    ).filter(GenerationLog.user_id == user_id)
    status = (status or "").strip()
    if status in STATUS_FILTERS:
        query = query.filter(GenerationLog.status == status)
    elif not include_failed:
        query = query.filter(has_output_clause())
    return query.order_by(GenerationLog.created_at.desc(), GenerationLog.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )


def serialize_log(log, *, image_url, reference_url):
    """一条记录 -> 列表项（网页端与 App 端的字段完全一致）。

    [image_url] / [reference_url] 由调用方注入：网页端用 `url_for`，App 端用写死的相对路径。
    网页端还要多一个 `detail_url`，由调用方补上。
    """
    img_count = 0 if log.status == "failed" else (log.count or 1)
    ref_count = log.references_count or 0
    return {
        "id": log.id,
        "first_image": image_url(log.id, 0) if img_count > 0 else "",
        "images": [image_url(log.id, i) for i in range(img_count)],
        "references": [reference_url(log.id, i) for i in range(ref_count)],
        "prompt": log.prompt,
        "model_name": log.model_name,
        "size": log.size or "auto",
        "count": log.count,
        "points_spent": points_to_str(log.points_spent),
        "status": log.status,
        "error": log.error or "",
        "created_at": (
            (log.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            if log.created_at
            else ""
        ),
    }
