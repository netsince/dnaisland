import json
import re

SUPPORTED_VERSION = 1
IMAGE_SLOTS = ("square", "landscape", "portrait")


def _deobfuscate(raw):
    """还原客户端 _lk / fx 字段的混淆（逆序 hex 拼接）。"""
    if not raw:
        return ""
    s = str(raw)
    reversed_hex = ""
    for i in range(0, len(s), 2):
        reversed_hex = s[i : i + 2] + reversed_hex
    try:
        return "".join(
            chr(int(m, 16)) for m in re.findall(r".{1,2}", reversed_hex)
        )
    except Exception:
        return s


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

    original_link = data.get("originalLink") or ""
    if not original_link and data.get("_lk"):
        original_link = _deobfuscate(data["_lk"])

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
        "original_link": original_link,
    }
