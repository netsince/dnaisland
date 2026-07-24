"""表情包公开 API：供前端面板与评论渲染使用。"""
from flask import Blueprint, jsonify

from ..models import Sticker, StickerSeries

sticker_bp = Blueprint("sticker", __name__, url_prefix="/stickers")


@sticker_bp.route("/api")
def api_list():
    """返回按系列分组的表情列表，供选择器与前端渲染使用。"""
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
                    {"code": st.code, "image_data": st.image_data}
                    for st in sorted(s.stickers, key=lambda x: (x.sort_order, x.id))
                ],
            }
        )
    return jsonify({"series": out})
