"""我的卡片：编辑 / 重新提审 / 隐藏 —— Web 与 App 共用的核心逻辑。

两端口径一致：编辑覆盖字段 + 标签/对话风格/图片整体替换 + 编辑后自动 re-pending；
仅被拒绝的卡可重提；隐藏仅作者本人可切换。
"""
import json

from ..extensions import db
from ..models import CardDialogueStyle, CardImage, CardTag
from ..services.card_publish_service import _normalize_images


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

    # 图片覆盖式更新（不做 export 专用压缩，与网页 edit 一致）
    CardImage.query.filter_by(card_id=card.id).delete()
    for slot, data_uri in _normalize_images(payload.get("images") or {}).items():
        db.session.add(CardImage(card_id=card.id, slot=slot, data=data_uri))

    db.session.commit()
    return None
