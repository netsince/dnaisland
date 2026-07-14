"""角色卡展示层数据装配。

统一「后端 → 模板」的卡片数据契约：模板只认卡片对象自身的属性，
不再需要路由额外传入 images / card_images 之类的旁路字典。

- attach_covers(cards): 批量为卡片对象附加 `cover`（方形封面 data URI 或 None）。
- load_card_images(card_id): 返回单卡 {slot: data} 图片字典。
"""

from datetime import datetime

from ..extensions import db
from ..models import Card, CardDialogueStyle, CardImage, CardTag
from sqlalchemy import func


def attach_covers(cards, slot="square"):
    """批量为一组卡片附加封面图，避免模板层 N 次查询或旁路字典。

    直接在卡片对象上写入 `cover` 属性（None 表示无图），返回原列表以便链式调用。
    """
    cards = list(cards or [])
    if not cards:
        return cards

    ids = [c.id for c in cards]
    covers = {
        img.card_id: img.data
        for img in CardImage.query.filter(
            CardImage.slot == slot, CardImage.card_id.in_(ids)
        ).all()
    }
    for c in cards:
        c.cover = covers.get(c.id)
    return cards


def load_card_images(card_id):
    """返回单张角色卡的全部图片，形如 {"square": data, "landscape": data, ...}。"""
    return {
        img.slot: img.data
        for img in CardImage.query.filter_by(card_id=card_id).all()
    }


def build_export_package(card):
    """把平台 Card 组装成 dna-client 可识别的 ExportPackage JSON 结构。

    字段严格对齐客户端 TaExportImportService 的导入逻辑
    （含 character.id / name / gender / persona / intro / opening /
    tags / dialogueStyle / images[slot].data），可被客户端「导入」直接识别。
    """
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = [
        {"user": d.user_text, "assistant": d.assistant_text}
        for d in CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
    ]
    image_rows = {
        img.slot: img.data
        for img in CardImage.query.filter_by(card_id=card.id).all()
        if img.data
    }

    # 保证三个图片槽位始终存在（缺失时为 null，客户端可正常解析）
    images = {
        slot: {"data": image_rows.get(slot)}
        for slot in ("square", "landscape", "portrait")
    }

    character = {
        "id": card.id,
        "name": card.name,
        "gender": card.gender or "无性",
        "persona": card.persona or "",
        "intro": card.intro or "",
        "opening": card.opening or "",
        "tags": tags,
        "dialogueStyle": dialogue,
        "images": images,
    }

    package = {
        "version": 1,
        "exportType": "single",
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "compressed": True,
        "character": character,
    }
    if card.original_link:
        package["originalLink"] = card.original_link
    return package


def popular_tags(viewer=None, limit=30):
    """返回可见卡片中的热门标签（含计数），按卡片数降序，用于探索页标签云。

    标签计数只统计对 viewer 可见的卡片，避免使用被屏蔽作者/未通过卡片的噪声标签。
    """
    visible_ids = Card.visible_to(viewer).with_entities(Card.id)
    rows = (
        db.session.query(CardTag.tag, func.count(CardTag.card_id).label("n"))
        .filter(CardTag.card_id.in_(visible_ids))
        .group_by(CardTag.tag)
        .order_by(func.count(CardTag.card_id).desc())
        .limit(limit)
        .all()
    )
    return [{"tag": r.tag, "count": r.n} for r in rows]
