"""我的卡片：编辑 / 重新提审 / 隐藏 —— Web 与 App 共用的核心逻辑。

两端口径一致：编辑覆盖字段 + 标签/对话风格/图片整体替换 + 编辑后自动 re-pending；
仅被拒绝的卡可重提；隐藏仅作者本人可切换。
"""

import json
from datetime import UTC, datetime

from ..extensions import db
from ..models import Card, CardDialogueStyle, CardImage, CardTag
from ..services.card_publish_service import (
    _normalize_author_note,
    _normalize_author_note_interval,
    _normalize_images,
    validate_image_slots,
)

# 每位作者最多可置顶的角色卡数量。
MAX_PINNED_CARDS = 2


def _utcnow_naive() -> datetime:
    """当前 UTC 时间（naive），与库中 server_default=now() 的口径一致。

    不用已废弃的 datetime.utcnow()。
    """
    return datetime.now(UTC).replace(tzinfo=None)


def set_card_pinned(viewer, card_id, pinned):
    """置顶 / 取消置顶角色卡。返回 (card, error)。

    error 取值：None | "无权操作此卡片" | "仅已通过的角色卡可以置顶" | "最多置顶 N 个角色卡"。
    置顶要求「已通过」：否则作者会占掉一个名额，而访客根本看不到那张卡。
    """
    card = db.session.get(Card, card_id)
    if not card or card.author_id != viewer.id:
        return None, "无权操作此卡片"

    if not pinned:
        card.pinned_at = None
        db.session.commit()
        return card, None

    if card.status != "approved":
        return None, "仅已通过的角色卡可以置顶"
    if card.pinned_at is not None:
        return card, None  # 已经置顶，幂等返回

    pinned_count = Card.query.filter(
        Card.author_id == viewer.id, Card.pinned_at.isnot(None)
    ).count()
    if pinned_count >= MAX_PINNED_CARDS:
        return None, f"最多置顶 {MAX_PINNED_CARDS} 个角色卡"

    card.pinned_at = _utcnow_naive()
    db.session.commit()
    return card, None


def resubmit_card(viewer, card):
    """重新提审（仅被拒绝的角色卡）。返回 error（非空表示条件不满足）。"""
    if card.author_id != viewer.id:
        return "无权操作此卡片"
    if card.status != "rejected":
        return "仅被拒绝的角色卡可以重新提审"
    card.status = "pending"
    db.session.commit()
    return None


def toggle_card_hidden(viewer, card):
    """切换隐藏状态（仅作者）。返回 error。"""
    if card.author_id != viewer.id:
        return "无权操作此卡片"
    card.is_hidden = not card.is_hidden
    db.session.commit()
    return None


def update_card_from_payload(card, payload):
    """用 payload 编辑 card（覆盖式）。返回 error（非空表示校验失败）。

    与网页 card_edit 一致：编辑后状态置 pending；标签/对话风格/图片整体替换，
    图片不做 export 专用压缩。
    """
    card.name = (payload.get("name") or "").strip() or card.name
    card.gender = payload.get("gender") or card.gender
    card.persona = payload.get("persona") or ""
    card.intro = payload.get("intro") or ""
    card.opening = payload.get("opening") or ""
    card.original_link = (payload.get("original_link") or "").strip() or None
    card.cover_focus = payload.get("cover_focus") or None
    # 作者注释：覆盖式更新，留空即清除（置 None，客户端自动回退到全局作者注释）
    card.author_note = _normalize_author_note(payload.get("author_note"))
    card.author_note_interval = _normalize_author_note_interval(
        payload.get("author_note_interval"),
        has_note=card.author_note is not None,
    )
    card.status = "pending"  # 编辑后自动重新提审

    # 标签覆盖式更新
    CardTag.query.filter_by(card_id=card.id).delete()
    for t in [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]:
        db.session.add(CardTag(card_id=card.id, tag=t))

    # 对话风格覆盖式更新
    CardDialogueStyle.query.filter_by(card_id=card.id).delete()
    ds_list = payload.get("dialogue_style") or []
    if isinstance(ds_list, str):
        try:
            ds_list = json.loads(ds_list)
        except json.JSONDecodeError:
            ds_list = []
    if isinstance(ds_list, list):
        for idx, item in enumerate(ds_list):
            if isinstance(item, dict):
                db.session.add(
                    CardDialogueStyle(
                        card_id=card.id,
                        turn_index=idx,
                        user_text=str(item.get("user") or ""),
                        assistant_text=str(item.get("assistant") or ""),
                    )
                )

    # 图片覆盖式更新（不做 export 专用压缩，与网页 edit 一致）。
    # 只校验「新增/被替换」的图：取值与既有图完全相同的槽位视为未改动，跳过比例校验，
    # 否则存量比例不合规的老卡片会因为一次编辑被卡死。
    incoming_images = dict(payload.get("images") or {})
    existing = {img.slot: img.data for img in CardImage.query.filter_by(card_id=card.id).all()}

    # App 端的卡详情接口不回传 base64（太大），`images` 给的是相对路径
    # `/card-image/<card_id>/<slot>`，编辑提交时 App 会把它原样带回。这里把
    # **本卡自己的路径**识别为「这张图没改」：直接用库里存的 data URL 原样保留 ——
    # 否则会被当成无效图片数据（data_url_to_bytes_and_mime 只认 data:），编辑接口直接 400，
    # 这正是「App 编辑角色卡无法提交」的根因；顺带也避免每次编辑都把图重编码掉一次画质。
    # 只认「本卡 + 对应槽位」的路径，其它取值（含别人的/伪造的路径）一律走原有校验。
    kept_images: dict[str, str] = {}
    for slot in list(incoming_images):
        value = incoming_images[slot]
        if isinstance(value, str) and value.strip() == f"/card-image/{card.id}/{slot}":
            stored = existing.get(slot)
            if stored:
                kept_images[slot] = stored
            del incoming_images[slot]

    try:
        validate_image_slots(incoming_images, unchanged=existing)
        normalized_images = _normalize_images(incoming_images)
    except ValueError as exc:
        return str(exc)

    CardImage.query.filter_by(card_id=card.id).delete()
    for slot, data_uri in {**normalized_images, **kept_images}.items():
        db.session.add(CardImage(card_id=card.id, slot=slot, data=data_uri))

    db.session.commit()
    return None
