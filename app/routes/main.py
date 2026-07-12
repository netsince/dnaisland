from flask import Blueprint, render_template, request

from ..extensions import db
from ..models import Card, CardImage, User

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Card.query.join(User, Card.author_id == User.id)
        .filter(Card.status == "approved", Card.is_hidden.is_(False), User.role != "banned")
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
