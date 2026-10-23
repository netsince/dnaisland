"""世界文本 → 编辑器表单 state。

网页端的发布/编辑页要能「粘贴 JSON / 上传 .dnaworld 文件」直接填表，所以需要
在服务端把三种格式统一解析成编辑器认识的结构：

1. **本应用导出的信封格式**：`{"exportType": "world", "world": {...}}`（.dnaworld 文件）
2. **裸 World JSON**：顶层就是 name / entries / tags
3. **第三方世界书**：`entries` 是以 uid 为键的对象，条目用 key / content / order /
   constant / disable（SillyTavern 那一套）

解析口径与客户端 WorldExportImportService 保持一致，免得「App 能导入、网页不能」。
"""

import json

# 编辑器里词条字段的默认值（缺字段时用它，保证 JS 侧拿到的结构一致）。
_ENTRY_DEFAULTS = {
    "keyRegex": None,
    "caseSensitive": False,
    "matchWholeWords": False,
    "recursive": False,
    "order": 0,
    "cooldownRounds": 0,
    "delayRounds": 0,
    "decorator": None,
}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _join_list(raw):
    if not isinstance(raw, list):
        return ""
    return ", ".join(str(x).strip() for x in raw if str(x).strip())


def _entry_from_world_json(entry):
    """本应用 World JSON 里的一条词条 → 编辑器词条。"""
    keys = entry.get("keys")
    item = {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "type": "person" if entry.get("type") == "person" else "noun",
        "keys": [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else [],
        "description": entry.get("description")
        if isinstance(entry.get("description"), str)
        else "",
        **_ENTRY_DEFAULTS,
    }
    # 人物专属字段：导入时必须原样带进编辑器，否则「导入 → 保存」一次就丢光了。
    relation = entry.get("relation")
    rel = None
    if isinstance(relation, dict):
        target_id = relation.get("targetId")
        content = relation.get("content")
        if (
            isinstance(target_id, str)
            and target_id
            and isinstance(content, str)
            and content.strip()
        ):
            rel = {"targetId": target_id, "content": content.strip()}

    item.update(
        {
            "keyRegex": entry.get("keyRegex") or None,
            "caseSensitive": bool(entry.get("caseSensitive")),
            "matchWholeWords": bool(entry.get("matchWholeWords")),
            "recursive": bool(entry.get("recursive")),
            "order": _as_int(entry.get("order")),
            "cooldownRounds": _as_int(entry.get("cooldownRounds")),
            "delayRounds": _as_int(entry.get("delayRounds")),
            "decorator": entry.get("decorator") or None,
            "gender": entry.get("gender")
            if entry.get("gender") in ("male", "female", "other")
            else None,
            "age": entry.get("age") if isinstance(entry.get("age"), str) else None,
            "status": entry.get("status")
            if entry.get("status") in ("normal", "dead")
            else None,
            "relation": rel,
        }
    )
    return item


def _state_from_world_json(data):
    entries = data.get("entries")
    clean = [
        _entry_from_world_json(e)
        for e in (entries if isinstance(entries, list) else [])
        if isinstance(e, dict)
    ]
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "summary": str(data.get("summary") or ""),
        "description": str(data.get("description") or ""),
        "tags": _join_list(data.get("tags")),
        "forbidden_words": _join_list(data.get("forbiddenWords")),
        "entries": clean,
    }


def _state_from_third_party(data):
    """第三方世界书（entries 为 uid → {key, content, ...}）→ 编辑器 state。"""
    clean = []
    for uid, raw in (data.get("entries") or {}).items():
        if not isinstance(raw, dict):
            continue
        if raw.get("disable") is True:
            continue
        key_raw = raw.get("key")
        keys = []
        if isinstance(key_raw, str):
            keys = [k.strip() for k in key_raw.split(",") if k.strip()]
        elif isinstance(key_raw, list):
            keys = [str(k).strip() for k in key_raw if str(k).strip()]
        content = raw.get("content") if isinstance(raw.get("content"), str) else ""
        if not keys and not content:
            continue
        item = {
            "id": str(uid),
            "name": keys[0] if keys else "",
            "type": "noun",
            "keys": keys[1:],
            "description": content,
            **_ENTRY_DEFAULTS,
        }
        item.update(
            {
                "order": _as_int(raw.get("order")),
                "cooldownRounds": _as_int(raw.get("cooldown")),
                "delayRounds": _as_int(raw.get("delay")),
                "caseSensitive": bool(raw.get("caseSensitive")),
                "matchWholeWords": bool(raw.get("matchWholeWords")),
                # constant = 常驻激活，对应装饰符 activate
                "decorator": "activate" if raw.get("constant") is True else None,
            }
        )
        clean.append(item)

    return {
        "id": "",
        "name": str(data.get("name") or "导入世界"),
        "summary": "",
        "description": "",
        "tags": _join_list(data.get("tags")),
        "forbidden_words": "",
        "entries": clean,
    }


def parse_imported_world(text):
    """解析一段世界文本。返回 (state, error)。

    state 是编辑器表单结构；出错时 state 为 None，error 是人话。
    """
    text = (text or "").strip()
    if not text:
        return None, "没有可识别的内容"
    try:
        data = json.loads(text)
    except ValueError:
        return None, "这不是合法的 JSON"
    if not isinstance(data, dict):
        return None, "这不是一个 JSON 对象"

    # 1) 本应用导出的信封格式
    inner = data.get("world")
    if isinstance(inner, dict):
        return _state_from_world_json(inner), None

    # 2) 第三方世界书（entries 是对象）
    if isinstance(data.get("entries"), dict):
        state = _state_from_third_party(data)
        if not state["entries"]:
            return None, "这份世界书里没有可用的词条"
        return state, None

    # 3) 裸 World JSON
    if "name" in data or isinstance(data.get("entries"), list):
        return _state_from_world_json(data), None

    return None, "没找到世界数据（既不是导出文件，也不是世界 JSON）"
