from flask import Blueprint, render_template, request
from flask_login import current_user

from ..models import Card, CardImage

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    # 信息层面统一过滤：被「屏蔽全部角色卡」作者的卡片不会出现在首页
    pagination = (
        Card.visible_to(current_user)
        .order_by(Card.view_count.desc(), Card.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    images = {
        i.card_id: i.data
        for i in CardImage.query.filter(
            CardImage.slot == "square",
            CardImage.card_id.in_([c.id for c in pagination.items]),
        ).all()
    }
    return render_template(
        "index.html",
        cards=pagination.items,
        pagination=pagination,
        args={},
        images=images,
    )
