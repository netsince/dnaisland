"""角色卡展示层数据装配。

统一「后端 → 模板」的卡片数据契约：模板只认卡片对象自身的属性，
不再需要路由额外传入 images / card_images 之类的旁路字典。

- attach_covers(cards): 批量为卡片对象附加 `cover`（方形封面 data URI 或 None）。
- load_card_images(card_id): 返回单卡 {slot: data} 图片字典。
"""

from ..extensions import db
from ..models import Card, CardImage, CardTag
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
