"""表情包服务：进程内缓存映射、文本渲染与提交清洗。"""
import re
from urllib.parse import quote

from ..caching import TimedCache
from ..models import Sticker

# [sticker:CODE]，CODE 允许中文、字母数字下划线连字符
_STICKER_TOKEN_RE = re.compile(r"\[sticker:([A-Za-z0-9_\u4e00-\u9fff-]+)\]")

# 进程内缓存 {code: image_data}，避免每条内容渲染都查库
_CACHE = TimedCache(ttl=300)
_CACHE_KEY = "sticker_map"


def get_sticker_map():
    """返回 {code: image_data} 的缓存映射；过期或失效后重新加载。

    数据库异常时降级返回上一次的映射（即使已过期），避免渲染中断。
    """
    m = _CACHE.get(_CACHE_KEY)
    if m is not None:
        return m
    try:
        m = {s.code: s.image_data for s in Sticker.query.all()}
    except Exception:
        m = _CACHE.get(_CACHE_KEY, stale=True) or {}
    _CACHE.set(_CACHE_KEY, m)
    return m


def invalidate_sticker_cache():
    """后台增删表情后调用，使映射立即失效。"""
    _CACHE.invalidate(_CACHE_KEY)


def render_stickers_html(text):
    """把文本中的 [sticker:CODE] 替换为 <img>（服务端渲染，供 linkify 调用）。"""
    if not text:
        return ""
    smap = get_sticker_map()

    def _repl(mm):
        code = mm.group(1)
        if code not in smap:
            return ""
        url = "/stickers/file/" + quote(code, safe="")
        return (
            f'<img class="dna-sticker" src="{url}" alt="{code}" title="{code}">'
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
