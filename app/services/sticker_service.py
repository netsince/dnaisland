"""表情包服务：进程内缓存映射、文本渲染与提交清洗。"""
import re
import time

from ..models import Sticker

# [sticker:CODE]，CODE 允许中文、字母数字下划线连字符
_STICKER_TOKEN_RE = re.compile(r"\[sticker:([A-Za-z0-9_\u4e00-\u9fff-]+)\]")

# 进程内缓存 {code: image_data}，避免每条内容渲染都查库
_CACHE = {"map": None, "at": 0.0}
_CACHE_TTL = 300  # 秒


def get_sticker_map():
    """返回 {code: image_data} 的缓存映射；过期或失效后重新加载。"""
    now = time.time()
    if _CACHE["map"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["map"]
    try:
        m = {s.code: s.image_data for s in Sticker.query.all()}
    except Exception:
        m = _CACHE["map"] or {}
    _CACHE["map"] = m
    _CACHE["at"] = now
    return m


def invalidate_sticker_cache():
    """后台增删表情后调用，使映射立即失效。"""
    _CACHE["map"] = None
    _CACHE["at"] = 0.0


def render_stickers_html(text):
    """把文本中的 [sticker:CODE] 替换为 <img>（服务端渲染，供 linkify 调用）。"""
    if not text:
        return ""
    smap = get_sticker_map()

    def _repl(mm):
        code = mm.group(1)
        data = smap.get(code)
        if not data:
            return ""
        return (
            f'<img class="dna-sticker" src="{data}" alt="{code}" title="{code}">'
        )

    return _STICKER_TOKEN_RE.sub(_repl, text)


def sanitize_stickers(text, max_count=20):
    """清洗提交内容：剔除不存在的 code，并对有效表情数量封顶。

    返回 (cleaned_text, used_count)。
    """
    if not text:
        return text, 0
    smap = get_sticker_map()
    count = [0]

    def _repl(mm):
        code = mm.group(1)
        if code not in smap:
            return ""
        if count[0] >= max_count:
            return ""
        count[0] += 1
        return mm.group(0)

    return _STICKER_TOKEN_RE.sub(_repl, text), count[0]
