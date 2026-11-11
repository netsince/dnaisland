"""图片压缩与裁剪工具。

统一将上传的图片处理为体积更小的 WebP（保留 alpha 通道、统一最长边、控制质量），
在观感损失极小的前提下最大化压缩，减少数据库存储与传输体积。
"""

import base64
import hashlib
import re
from io import BytesIO

from PIL import Image, ImageSequence

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


_MIME_BY_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_mime(raw: bytes) -> str | None:
    """按魔数判定真实图片格式；认不出来返回 None。

    不能相信 data URL 里写的 mime：上游生图接口会返回 GIF，而调用方过去一律
    当成 PNG 拼前缀，前缀与实际内容对不上（见 image_gen_service._extract_images）。
    """
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _MIME_BY_MAGIC:
        if raw.startswith(magic):
            return mime
    return None


def _is_animated(img) -> bool:
    return bool(getattr(img, "is_animated", False)) and getattr(img, "n_frames", 1) > 1


def _fit(frame, max_edge: int):
    if not max_edge or max(frame.size) <= max_edge:
        return frame
    scale = max_edge / max(frame.size)
    return frame.resize(
        (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _animated_to_webp(img, max_edge: int, quality: int) -> bytes:
    """逐帧转 RGBA 后整段写成动图 WebP。

    **必须**用 save_all + append_images：只 save 一次的话 Pillow 只写第一帧，
    GIF 其余帧会静默丢失 —— 线上表现就是「生成的动图只剩一张静止图」。
    """
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(img):
        frames.append(_fit(frame.convert("RGBA"), max_edge))
        durations.append(frame.info.get("duration", img.info.get("duration", 100)))
    buf = BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        quality=quality,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=img.info.get("loop", 0),
    )
    return buf.getvalue()


def image_bytes_to_webp(raw: bytes, max_edge: int = 1024, quality: int = 82) -> bytes | None:
    """任意常见图片格式 -> WebP 字节；动图保留全部帧。转不了返回 None。"""
    try:
        img = Image.open(BytesIO(raw))
    except Exception:
        return None
    try:
        if _is_animated(img):
            return _animated_to_webp(img, max_edge=max_edge, quality=quality)
        still = img if img.mode in ("RGB", "RGBA") else img.convert("RGBA")
        still = _fit(still, max_edge)
        buf = BytesIO()
        still.save(buf, format="WEBP", quality=quality)
        return buf.getvalue()
    except Exception:
        return None


def image_bytes_to_webp_data_url(
    raw: bytes, max_edge: int = 1024, quality: int = 90
) -> str:
    """任意格式字节 -> WebP data URL（动图保留帧）。

    转换失败时按**真实格式**拼 data URL 原样返回：宁可存一份原图，
    也不要存一份前缀写错的图（那会让下游把它当成 WebP 直接透传）。
    """
    webp = image_bytes_to_webp(raw, max_edge=max_edge, quality=quality)
    if webp is not None:
        return "data:image/webp;base64," + base64.b64encode(webp).decode("ascii")
    mime = sniff_image_mime(raw) or "image/png"
    return "data:{};base64,{}".format(mime, base64.b64encode(raw).decode("ascii"))


def _response_mime(data: bytes) -> str:
    """响应用的 Content-Type：按真实字节判定，转换失败时不会标错。"""
    return sniff_image_mime(data) or "image/webp"


def _encode_webp_uncached(data_url: str, max_edge: int = 1024, quality: int = 82) -> bytes:
    """无缓存的底层转码实现（见 data_url_to_webp_bytes）。"""
    raw, _ = data_url_to_bytes_and_mime(data_url)
    # 已经是 WebP（含动图）就原样透传，避免重复压缩掉画质。
    if sniff_image_mime(raw) == "image/webp":
        return raw
    converted = image_bytes_to_webp(raw, max_edge=max_edge, quality=quality)
    return converted if converted is not None else raw


# 公开图片的缓存时长。生成产物写出后不再变更，可以给较长的时间；
# 配合 ETag，过期后也只是一次 304，不会重传字节。
PUBLIC_IMAGE_MAX_AGE = 604800  # 7 天


def send_public_webp(
    data_url: str,
    max_edge: int = 1024,
    quality: int = 82,
    max_age: int = PUBLIC_IMAGE_MAX_AGE,
):
    """以「公开可缓存」方式返回图片字节。

    与 [send_webp] 的区别只在响应头：
      - ``Cache-Control: public, max-age=...`` —— 允许 Cloudflare 边缘缓存
      - ``ETag`` —— 过期后 304，不重传字节

    调用方必须保证该 URL 不含用户私有语义（不随登录状态变化），
    否则会被 CDN 把 A 的图发给 B。
    """
    from flask import Response, request

    if not data_url:
        return Response(status=404)
    webp = data_url_to_webp_bytes(data_url, max_edge=max_edge, quality=quality)
    etag = '"' + hashlib.md5(webp).hexdigest() + '"'
    cache_control = f"public, max-age={max_age}"
    # 直接比原始头：werkzeug 的 ETags 容器对带引号的字符串匹配不可靠，
    # 用子串判断对单值/多值都成立。
    if_none_match = request.headers.get("If-None-Match", "")
    if etag in if_none_match:
        return Response(
            status=304,
            headers={"ETag": etag, "Cache-Control": cache_control},
        )
    resp = Response(webp, mimetype=_response_mime(webp))
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = cache_control
    return resp

def send_webp(data_url: str, max_edge: int = 1024, quality: int = 82):
    """将存储的 Data URL 转为 WebP 字节并以 image/webp 响应返回。

    统一替代各路由中重复的 send_file(BytesIO(data_url_to_webp_bytes(...))) 写法。
    """
    from flask import send_file

    if not data_url:
        return ("", 404)
    webp = data_url_to_webp_bytes(data_url, max_edge=max_edge, quality=quality)
    return send_file(BytesIO(webp), mimetype=_response_mime(webp), max_age=86400)


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
