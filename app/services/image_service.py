"""图片压缩与裁剪工具。

统一将上传的图片处理为体积更小的 WebP（保留 alpha 通道、统一最长边、控制质量），
在观感损失极小的前提下最大化压缩，减少数据库存储与传输体积。
"""
import base64
import re
from io import BytesIO

from PIL import Image

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)


def _decode(data_url: str) -> bytes:
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise ValueError("无效的图片数据")
    return base64.b64decode(m.group("data"))


def _encode(raw: bytes, mime: str = "image/webp") -> str:
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


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
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")


def crop_square_and_compress(data_url: str, size: int = 256, quality: int = 82) -> str:
    """居中裁剪为正方形并压缩为 WebP，用于头像。"""
    raw = _decode(data_url)
    img = Image.open(BytesIO(raw)).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((size, size), Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")


def raw_bytes_to_webp_data_url(raw_bytes: bytes, max_edge: int = 1024, quality: int = 80) -> str:
    """将原始图片字节流压缩为最长边不超过 max_edge 的 WebP base64 Data URL。"""
    img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return _encode(out.getvalue(), mime="image/webp")

