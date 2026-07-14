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
        raise ValueError("JSON 格式无效，无法解析")

    if not isinstance(data, dict):
        raise ValueError("无效的导出文件格式")

    version = data.get("version")
    if not isinstance(version, int) or version > SUPPORTED_VERSION:
        raise ValueError(f"不支持的版本: {version}（当前支持: {SUPPORTED_VERSION}）")

    character = data.get("character")
    if not isinstance(character, dict):
        raise ValueError("缺少 character 字段")

    # 版权溯源保护检测：命中任意一层保护即拦截（原封不动对齐 island 平台策略）
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
    if isinstance(tags, list):
        tags = [str(t) for t in tags]
    else:
        tags = []

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
        # 导入成功后强制清空源链接（对齐 island：通过检测的卡视为无原作者标记）
        "original_link": "",
    }
