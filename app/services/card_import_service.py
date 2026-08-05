import json

SUPPORTED_VERSION = 1
IMAGE_SLOTS = ("square", "landscape", "portrait")


def parse_export_package(json_str: str) -> dict:
    """解析客户端导出的 ExportPackage JSON，返回扁平化的角色卡字段。

    失败抛出 ValueError（消息面向用户）。
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError("JSON 格式无效，无法解析") from None

    if not isinstance(data, dict):
        raise ValueError("无效的导出文件格式")

    version = data.get("version")
    if not isinstance(version, int) or version > SUPPORTED_VERSION:
        raise ValueError(f"不支持的版本: {version}（当前支持: {SUPPORTED_VERSION}）")

    character = data.get("character")
    if not isinstance(character, dict):
        raise ValueError("缺少 character 字段")

    # 版权溯源保护检测：命中任意一层保护即拦截
    has_original_link = bool(str(data.get("originalLink") or "").strip())
    has_hidden_lk = bool(str(data.get("_lk") or "").strip())
    has_image_fx = False
    raw_images_check = character.get("images") or data.get("images")
    if isinstance(raw_images_check, dict):
        for slot in IMAGE_SLOTS:
            img = raw_images_check.get(slot)
            if isinstance(img, dict) and str(img.get("fx") or "").strip():
                has_image_fx = True
                break
    if has_original_link or has_hidden_lk or has_image_fx:
        raise ValueError("有原作者的角色卡")

    images = {}
    raw_images = character.get("images")
    if isinstance(raw_images, dict):
        for slot in IMAGE_SLOTS:
            img = raw_images.get(slot)
            if isinstance(img, dict):
                images[slot] = img.get("data") or ""
            elif isinstance(img, str):
                images[slot] = img

    dialogue_style = []
    raw_ds = character.get("dialogueStyle")
    if isinstance(raw_ds, list):
        for item in raw_ds:
            if isinstance(item, dict):
                dialogue_style.append(
                    {
                        "user": item.get("user") or "",
                        "assistant": item.get("assistant") or "",
                    }
                )

    tags = character.get("tags") or []
    tags = [str(t) for t in tags] if isinstance(tags, list) else []

    # 语音合成 seed：优先读顶层 seed，其次 character.voiceSeed / 顶层 voiceSeed。
    # 非整数（或缺失）时置为 None（发布页 seed 为非必填项）。
    seed = data.get("seed")
    if not isinstance(seed, int):
        seed = character.get("voiceSeed")
    if not isinstance(seed, int):
        seed = data.get("voiceSeed")
    seed = seed if isinstance(seed, int) else None

    # 角色卡绑定的作者注释（可选）：从 character.authorNote 读取。
    # 仅当为非空字符串时启用，否则置 None（表示未设置，客户端自动回退到全局作者注释）。
    raw_note = character.get("authorNote")
    author_note = (
        str(raw_note).strip() if isinstance(raw_note, str) and raw_note.strip() else None
    )
    # 注入间隔：仅当作者注释启用时有效；非正整数（或缺失）视为禁用（0）。
    raw_interval = character.get("authorNoteInterval")
    if isinstance(raw_interval, int) and raw_interval > 0 and author_note is not None:
        author_note_interval = raw_interval
    else:
        author_note_interval = 0

    # 注意：不读取 JSON 中的 id，平台始终自动分配新 id
    return {
        "name": character.get("name") or "",
        "gender": character.get("gender") or "无性",
        "persona": character.get("persona") or "",
        "intro": character.get("intro") or "",
        "opening": character.get("opening") or "",
        "tags": tags,
        "dialogue_style": dialogue_style,
        "images": images,
        "seed": seed,
        "author_note": author_note,
        "author_note_interval": author_note_interval,
        # 导入成功后强制清空源链接
        "original_link": "",
    }
