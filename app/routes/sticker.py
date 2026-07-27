"""表情包公开 API：供前端面板与评论渲染使用。"""
import base64
import contextlib
import hashlib

from flask import Blueprint, Response, jsonify, request

from ..models import Sticker, StickerSeries

sticker_bp = Blueprint("sticker", __name__, url_prefix="/stickers")


@sticker_bp.route("/api")
def api_list():
    """返回按系列分组的表情列表，供选择器与前端渲染使用。

    只返回轻量元数据 + 图片 URL，不内联 base64，避免响应体过大导致面板卡顿。
    """
    series = (
        StickerSeries.query.order_by(StickerSeries.sort_order, StickerSeries.id)
        .all()
    )
    out = []
    for s in series:
        out.append(
            {
                "slug": s.slug,
                "name": s.name,
                "stickers": [
                    {"code": st.code, "image_url": f"/stickers/file/{st.code}"}
                    for st in sorted(s.stickers, key=lambda x: (x.sort_order, x.id))
                ],
            }
        )
    return jsonify({"series": out})


@sticker_bp.route("/file/<code>")
def sticker_file(code):
    """按表情 ID 返回图片字节，供前端逐张懒加载（加载一张显示一张）。

    通过 ETag + Cache-Control 让浏览器把图片缓存到本地：同一会话重复打开
    表情面板时，图片直接从本地缓存读取，不再回源请求。
    """
    st = Sticker.query.filter_by(code=code).first_or_404()
    raw = st.image_data
    mime = "image/webp"
    if raw.startswith("data:"):
        header, _, b64 = raw.partition(",")
        with contextlib.suppress(ValueError):
            mime = header[5 : header.index(";")] or mime
        data = base64.b64decode(b64)
    else:
        data = base64.b64decode(raw)
    etag = '"' + hashlib.md5(data).hexdigest() + '"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["ETag"] = etag
    return resp
