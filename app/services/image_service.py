"""图片压缩与裁剪工具。

统一将上传的图片处理为体积更小的 WebP（保留 alpha 通道、统一最长边、控制质量），
在观感损失极小的前提下最大化压缩，减少数据库存储与传输体积。
"""

import base64
import hashlib
import re
from io import BytesIO

from PIL import Image

from ..caching import TimedCache

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)


def _decode(data_url: str) -> bytes:
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise ValueError("无效的图片数据")
    return base64.b64decode(m.group("data"))


def _encode(raw: bytes, mime: str = "image/webp") -> str:
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def image_size(raw_bytes: bytes) -> tuple[int, int]:
    """读取图片的原始像素尺寸，返回 (宽, 高)。无法识别时抛 ValueError（消息面向用户）。"""
    try:
        with Image.open(BytesIO(raw_bytes)) as img:
            return img.size
    except Exception:
        raise ValueError("图片无法识别，请重新选择") from None


def compress_image(data_url: str, max_edge: int = 1024, quality: int = 80) -> str:
    """压缩任意比例为 WebP。

    - 保留透明通道（统一转 RGBA）
    - 最长边超过 max_edge 时等比缩放
    - quality 控制 WebP 压缩质量
    """
    raw = _decode(data_url)
    img = Image.open(BytesIO(raw)).convert("RGBA")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")


def crop_square_and_compress(data_url: str, size: int = 256, quality: int = 82) -> str:
    """居中裁剪为正方形并压缩为 WebP，用于头像。输入为 base64 data URL。"""
    raw = _decode(data_url)
    return crop_square_and_compress_bytes(raw, size=size, quality=quality)


def crop_square_and_compress_bytes(raw_bytes: bytes, size: int = 256, quality: int = 82) -> str:
    """从原始图片字节居中裁剪为正方形并压缩为 WebP，用于头像（与 crop_square_and_compress 等价，输入为字节）。"""
    img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")


def raw_bytes_to_webp_data_url(raw_bytes: bytes, max_edge: int = 1024, quality: int = 80) -> str:
    """将原始图片字节流压缩为最长边不超过 max_edge 的 WebP base64 Data URL。"""
    img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")


def data_url_to_bytes_and_mime(data_url: str) -> tuple[bytes, str]:
    """高效从 base64 Data URL 提取原始 bytes 字节与 mimetype（无 PIL 重度开销）。"""
    if not data_url:
        raise ValueError("无效的图片数据")
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise ValueError("无效的图片数据")
    mime = m.group("mime") or "image/webp"
    raw = base64.b64decode(m.group("data"))
    return raw, mime


# 图片转码结果缓存：图片在库中以 base64 存储，每次请求都要解码（甚至 PIL 重编码）。
# 图片不变则命中，避免重复 CPU 重算。
#
# 容量必须按**字节**封顶而不是按条数：本缓存装的是整张图片字节，库内单条
# reference_images 平均约 1.4MB。若只按 500 条封顶，常驻内存可达数百 MB —— 这正是
# 线上 5012 两个 worker 各占约 210MB 的原因。32MB 预算下，即使全部是 1.4MB 的大图
# 也只驻留约 23 张，小而热的图则能放下更多，符合「按体积而非按个数」的内存约束。
_WEBP_CACHE_MAXBYTES = 32 * 1024 * 1024
_WEBP_CACHE = TimedCache(ttl=3600, maxsize=500, maxbytes=_WEBP_CACHE_MAXBYTES)


def _cache_key(data_url: str, max_edge: int, quality: int) -> str:
    """按 (max_edge, quality, 图片内容) 生成缓存键。

    用增量 update 而不是 f-string 拼接：后者会为每条图片构造一个与 base64 等长的
    临时字符串（单条可达数 MB），在高并发图片请求下这段瞬时峰值相当可观。
    """
    h = hashlib.sha1()
    h.update(b"v1|")
    h.update(str(max_edge).encode())
    h.update(b"|")
    h.update(str(quality).encode())
    h.update(b"|")
    h.update(data_url.encode("utf-8", "surrogatepass"))
    return h.hexdigest()


def data_url_to_webp_bytes(data_url: str, max_edge: int = 1024, quality: int = 82) -> bytes:
    """存储的 base64 Data URL -> 图片原始字节（供图片 API 直接二进制响应）。

    对于库内存储的 Data URL（几乎全部为 WebP/PNG/JPEG），直接提取解码原始 bytes
    发送；非 WebP 才触发 PIL 重编码。结果按 (data_url, max_edge, quality) 缓存，
    避免每次 HTTP GET 都重复解码/重编码。
    """
    key = _cache_key(data_url, max_edge, quality)
    cached = _WEBP_CACHE.get(key)
    if cached is not None:
        return cached
    webp = _encode_webp_uncached(data_url, max_edge=max_edge, quality=quality)
    _WEBP_CACHE.set(key, webp)
    return webp


def _encode_webp_uncached(data_url: str, max_edge: int = 1024, quality: int = 82) -> bytes:
    """无缓存的底层转码实现（见 data_url_to_webp_bytes）。"""
    raw, _ = data_url_to_bytes_and_mime(data_url)
    if data_url.startswith("data:image/"):
        return raw
    try:
        img = Image.open(BytesIO(raw)).convert("RGBA")
        w, h = img.size
        if max(w, h) > max_edge:
            scale = max_edge / max(w, h)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
            )
        out = BytesIO()
        img.save(out, format="WEBP", quality=quality)
        return out.getvalue()
    except Exception:
        return raw


def send_webp(data_url: str, max_edge: int = 1024, quality: int = 82):
    """将存储的 Data URL 转为 WebP 字节并以 image/webp 响应返回。

    统一替代各路由中重复的 send_file(BytesIO(data_url_to_webp_bytes(...))) 写法。
    """
    from flask import send_file

    if not data_url:
        return ("", 404)
    webp = data_url_to_webp_bytes(data_url, max_edge=max_edge, quality=quality)
    return send_file(BytesIO(webp), mimetype="image/webp", max_age=86400)


# 复制到剪贴板导出专用的轻度压缩参数：控制在剪贴板体积上限内，但不过度损失观感。
# 这三个槽位都会内联进 JSON 文本，体积过大会导致浏览器写入剪贴板失败，故适度缩小。
EXPORT_OPTIMIZE_MAX_EDGE = 768
EXPORT_OPTIMIZE_QUALITY = 82


def optimize_image_for_export(
    data_url: str, max_edge: int = EXPORT_OPTIMIZE_MAX_EDGE, quality: int = EXPORT_OPTIMIZE_QUALITY
) -> str:
    """为“复制到剪贴板”导出准备：轻度压缩为 WebP，控制体积但保留可识别度。

    用于两种场景：
    1. 发布上传时即对图片做此轻度优化，并打上 optimized 标记；
    2. 首次复制导出时，对未优化的图片再做一次优化并写回数据库、打上标记。
    解码或重编码失败时原样返回，避免阻断导出流程。
    """
    if not data_url:
        return data_url
    try:
        return compress_image(data_url, max_edge=max_edge, quality=quality)
    except Exception:
        return data_url
