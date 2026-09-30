"""角色卡发布（新建）/ 解析导入：Web 与 App 共用的核心逻辑。

Web 端 /publish/edit 与 App 端 /api/v1/cards/publish 都调用 create_card_from_payload，
保证字段处理、图片压缩、审核状态等完全一致；解析导入直接复用 card_import_service。
"""
import hashlib
import json
import uuid

from ..extensions import db
from ..models import Card, CardDialogueStyle, CardImage, CardTag
from ..services.image_service import (
    compress_image,
    data_url_to_bytes_and_mime,
    image_size,
    optimize_image_for_export,
    raw_bytes_to_webp_data_url,
)

IMAGE_SLOTS = ("square", "landscape", "portrait")

# 各槽位要求的宽高比 (宽, 高)。这是「图片比例限制」在服务端的唯一权威定义。
# 前端必须与之一致：
#   App：card_publish_page.dart 的 _imageSlots（CropAspect）
#   Web：publish/edit.html 的 ASPECT
# 客户端的裁剪锁定只是 UI 约束，可被绕过；这里才是真正的强制点。
SLOT_ASPECT_RATIOS = {
    "square": (1, 1),
    "landscape": (16, 9),
    "portrait": (9, 16),
}
SLOT_LABELS = {
    "square": "1:1 形象",
    "landscape": "16:9 形象（头图）",
    "portrait": "9:16 形象",
}
# 裁剪器取整与编码会带来微小偏差，允许 ±2%。
ASPECT_TOLERANCE = 0.02


def _content_fingerprint(
    *,
    name,
    gender,
    persona,
    intro,
    opening,
    original_link,
    seed,
    author_note,
    author_note_interval,
    tags,
    dialogue_style,
):
    """对规范化后的文本字段生成内容指纹（sha256）。

    图片 base64 因重复编码可能略有差异，故不纳入指纹，只比较决定卡片内容的
    文本字段。排序后的 tags 与 dialogue_style 保证提交顺序不影响指纹，
    相同内容的卡无论点击多少次都会得到同一指纹，用于幂等去重。
    """
    payload = {
        "name": name,
        "gender": gender,
        "persona": persona,
        "intro": intro,
        "opening": opening,
        "original_link": original_link,
        "seed": seed,
        "author_note": author_note,
        "author_note_interval": author_note_interval,
        "tags": sorted(tags),
        "dialogue_style": dialogue_style,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _image_bytes_from_payload(val) -> bytes:
    """把槽位取值（原始字节或 data URL 字符串）转成图片字节；空值返回 b""。"""
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if isinstance(val, str) and val.strip():
        raw, _ = data_url_to_bytes_and_mime(val)
        return raw
    return b""


def _validate_slot_aspect(slot: str, raw: bytes) -> None:
    """校验图片宽高比是否符合槽位要求；不符合抛 ValueError（消息面向用户）。"""
    spec = SLOT_ASPECT_RATIOS.get(slot)
    if spec is None:
        return
    width, height = image_size(raw)
    if width <= 0 or height <= 0:
        raise ValueError(f"{SLOT_LABELS.get(slot, slot)} 图片尺寸无效，请重新选择")
    expected = spec[0] / spec[1]
    actual = width / height
    if abs(actual - expected) / expected > ASPECT_TOLERANCE:
        raise ValueError(
            f"{SLOT_LABELS.get(slot, slot)} 需要 {spec[0]}:{spec[1]} 比例，"
            f"当前图片为 {width}×{height}，请重新裁剪后上传"
        )


def validate_image_slots(images, *, unchanged=None) -> None:
    """校验图片槽位的宽高比，不符合抛 ValueError。

    [unchanged] 为 {slot: 既有 data URL}：取值与该槽位既有图**完全相同**的视为「未改动」，
    跳过校验。这样编辑老卡片时，不会因为存量图片比例不合规而被卡住（只约束新上传/替换的图）。
    新建时所有图都是新的，传 unchanged=None 即全量校验。
    """
    unchanged = unchanged or {}
    for slot in IMAGE_SLOTS:
        val = (images or {}).get(slot)
        raw = _image_bytes_from_payload(val)
        if not raw:
            continue
        prev = unchanged.get(slot)
        if isinstance(val, str) and prev is not None and val == prev:
            continue
        _validate_slot_aspect(slot, raw)


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

    # 新建时所有图片都是新上传的，全量校验比例；不合规则返回用户可读错误。
    try:
        validate_image_slots(payload.get("images") or {})
        images = _normalize_images(payload.get("images") or {}, optimize=True)
    except ValueError as exc:
        return None, str(exc)

    author_note = _normalize_author_note(payload.get("author_note"))
    author_note_interval = _normalize_author_note_interval(
        payload.get("author_note_interval"), has_note=author_note is not None
    )

    name = (payload.get("name") or "").strip()
    persona = payload.get("persona") or ""
    intro = payload.get("intro") or ""
    opening = payload.get("opening") or ""
    original_link = (payload.get("original_link") or "").strip() or None
    seed = _normalize_seed(payload.get("seed"))

    # 幂等去重：同一作者若已存在相同内容指纹的「待审核」卡，直接复用，
    # 避免重复点击 / 网络重试导致产生多份一模一样却各自独立的卡片。
    content_hash = _content_fingerprint(
        name=name,
        gender=gender,
        persona=persona,
        intro=intro,
        opening=opening,
        original_link=original_link,
        seed=seed,
        author_note=author_note,
        author_note_interval=author_note_interval,
        tags=tags,
        dialogue_style=dialogue_style,
    )
    existing = Card.query.filter_by(
        author_id=author.id, content_hash=content_hash, status="pending"
    ).first()
    if existing:
        return existing, None

    card = Card(
        id=card_id,
        author_id=author.id,
        name=name,
        gender=gender,
        persona=persona,
        intro=intro,
        opening=opening,
        original_link=original_link,
        cover_focus=payload.get("cover_focus") or None,
        seed=seed,
        author_note=author_note,
        author_note_interval=author_note_interval,
        content_hash=content_hash,
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
