"""角色卡发布（新建）/ 解析导入：Web 与 App 共用的核心逻辑。

Web 端 /publish/edit 与 App 端 /api/v1/cards/publish 都调用 create_card_from_payload，
保证字段处理、图片压缩、审核状态等完全一致；解析导入直接复用 card_import_service。
"""
import uuid

from ..extensions import db
from ..models import Card, CardDialogueStyle, CardImage, CardTag
from ..services.image_service import (
    compress_image,
    optimize_image_for_export,
    raw_bytes_to_webp_data_url,
)

IMAGE_SLOTS = ("square", "landscape", "portrait")


def _normalize_seed(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _normalize_author_note(raw_note):
    """规范化作者注释：仅保留非空字符串，否则置 None（表示未设置）。"""
    if isinstance(raw_note, str) and raw_note.strip():
        return raw_note.strip()
    return None


def _normalize_author_note_interval(raw_interval, has_note):
    """规范化注入间隔：需为正整数，且作者注释已设置，否则视为 0（禁用）。"""
    if not has_note:
        return 0
    if isinstance(raw_interval, int):
        return raw_interval if raw_interval > 0 else 0
    if isinstance(raw_interval, str):
        try:
            val = int(raw_interval)
            return val if val > 0 else 0
        except ValueError:
            return 0
    return 0


def _normalize_images(images, optimize=False):
    """images: {slot: bytes | data_url_str} → {slot: data_url_str}。

    字节视为新上传，转 webp；字符串（data url）视为已有图，重新压缩。
    optimize=True 时对结果再做 export 专用轻度压缩（发布新建用）。
    """
    out = {}
    for slot in IMAGE_SLOTS:
        val = images.get(slot)
        if isinstance(val, (bytes, bytearray)):
            if not val:
                continue
            data_uri = raw_bytes_to_webp_data_url(bytes(val), max_edge=1024, quality=80)
        elif isinstance(val, str) and val.strip():
            data_uri = compress_image(val)
        else:
            continue
        if optimize:
            data_uri = optimize_image_for_export(data_uri)
        out[slot] = data_uri
    return out


def create_card_from_payload(author, payload):
    """用 payload 创建一张待审核的角色卡。返回 (card, error)。

    payload 字段：name, gender, persona, intro, opening, original_link,
    cover_focus, seed, tags(list), dialogue_style(list[{user,assistant}]),
    images(dict slot->bytes|data_url)。
    """
    card_id = str(uuid.uuid4())
    gender = (payload.get("gender") or "").strip() or "无性"
    tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]

    dialogue_style = []
    ds_list = payload.get("dialogue_style") or []
    if isinstance(ds_list, list):
        for item in ds_list:
            if isinstance(item, dict):
                dialogue_style.append(
                    {
                        "user": str(item.get("user") or ""),
                        "assistant": str(item.get("assistant") or ""),
                    }
                )

    images = _normalize_images(payload.get("images") or {}, optimize=True)

    author_note = _normalize_author_note(payload.get("author_note"))
    author_note_interval = _normalize_author_note_interval(
        payload.get("author_note_interval"), has_note=author_note is not None
    )

    card = Card(
        id=card_id,
        author_id=author.id,
        name=(payload.get("name") or "").strip(),
        gender=gender,
        persona=payload.get("persona") or "",
        intro=payload.get("intro") or "",
        opening=payload.get("opening") or "",
        original_link=(payload.get("original_link") or "").strip() or None,
        cover_focus=payload.get("cover_focus") or None,
        seed=_normalize_seed(payload.get("seed")),
        author_note=author_note,
        author_note_interval=author_note_interval,
        status="pending",  # 未审核
    )
    db.session.add(card)
    for tag in tags:
        db.session.add(CardTag(card_id=card_id, tag=tag))
    for idx, turn in enumerate(dialogue_style):
        db.session.add(
            CardDialogueStyle(
                card_id=card_id,
                turn_index=idx,
                user_text=turn["user"],
                assistant_text=turn["assistant"],
            )
        )
    for slot, data_uri in images.items():
        optimized_data = optimize_image_for_export(data_uri)
        db.session.add(
            CardImage(card_id=card_id, slot=slot, data=optimized_data, optimized=True)
        )
    return card, None
