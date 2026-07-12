from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, or_

from ..extensions import db
from ..models import Card, CardTag, Punishment, User
from ..services.card_service import attach_covers

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
    return render_template(
        "index.html",
        cards=attach_covers(pagination.items),
        pagination=pagination,
        args={},
    )


def _banned_author_ids():
    """处于 profile_banned 处罚的作者，其主页不可被搜索到。"""
    return db.session.query(Punishment.user_id).filter(
        Punishment.status == "active", Punishment.type == "profile_banned"
    ).distinct()


def _card_search_query(q, sort, tag=None):
    """构造角色卡检索查询（已包含信息层可见性过滤与相关度排序）。"""
    like = f"%{q}%"
    base = Card.visible_to(current_user).outerjoin(
        CardTag, CardTag.card_id == Card.id
    )
    filters = [
        or_(
            Card.name.like(like),
            Card.intro.like(like),
            Card.persona.like(like),
            CardTag.tag.like(like),
        )
    ]
    if tag:
        filters.append(CardTag.tag == tag)
    base = base.filter(*filters).distinct()

    if sort == "hot":
        order = [Card.view_count.desc(), Card.created_at.desc()]
    elif sort == "new":
        order = [Card.created_at.desc()]
    else:  # relevance
        score = case(
            (Card.name.like(like), 3),
            (CardTag.tag.like(like), 2),
            (or_(Card.intro.like(like), Card.persona.like(like)), 1),
            else_=0,
        )
        order = [score.desc(), Card.view_count.desc(), Card.created_at.desc()]
    return base.order_by(*order)


def _user_search_query(q, sort):
    """构造作者检索查询。"""
    like = f"%{q}%"
    banned = _banned_author_ids()
    base = User.query.filter(
        User.status == "active",
        User.id.notin_(banned),
        or_(
            User.nickname.like(like),
            User.username.like(like),
            User.bio.like(like),
        ),
    )
    if sort == "new":
        order = [User.created_at.desc()]
    else:  # relevance：昵称命中优先
        score = case(
            (User.nickname.like(like), 2),
            (or_(User.username.like(like), User.bio.like(like)), 1),
            else_=0,
        )
        order = [score.desc(), User.created_at.desc()]
    return base.order_by(*order)


@main_bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    search_type = request.args.get("type", "all")
    sort = request.args.get("sort", "relevance")
    tag = (request.args.get("tag") or "").strip() or None
    page = request.args.get("page", 1, type=int)

    valid_types = ("all", "card", "user")
    if search_type not in valid_types:
        search_type = "all"
    valid_sorts = ("relevance", "hot", "new")
    if sort not in valid_sorts:
        sort = "relevance"

    args = {"q": q, "type": search_type, "sort": sort}
    if tag:
        args["tag"] = tag

    cards = []
    cards_pagination = None
    users = []
    users_pagination = None

    if q and search_type in ("all", "card"):
        cards_pagination = _card_search_query(q, sort, tag).paginate(
            page=page, per_page=12, error_out=False
        )
        cards = attach_covers(cards_pagination.items)

    if q and search_type in ("all", "user"):
        users_pagination = _user_search_query(q, sort).paginate(
            page=page, per_page=20, error_out=False
        )
        users = users_pagination.items

    return render_template(
        "search.html",
        q=q,
        search_query=q,
        search_type=search_type,
        sort=sort,
        tag=tag,
        cards=cards,
        cards_pagination=cards_pagination,
        users=users,
        users_pagination=users_pagination,
        args=args,
    )


@main_bp.route("/search/suggest")
def search_suggest():
    """顶栏实时下拉建议：返回匹配度最高的若干角色卡与作者。"""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"cards": [], "users": []})
    like = f"%{q}%"

    cards = (
        _card_search_query(q, "relevance")
        .limit(6)
        .all()
    )
    card_hits = [
        {"id": c.id, "name": c.name, "gender": c.gender, "url": url_for("user.card_detail", card_id=c.id)}
        for c in cards
    ]

    users = _user_search_query(q, "relevance").limit(5).all()
    user_hits = [
        {
            "username": u.username,
            "nickname": u.nickname,
            "verified": bool(u.verified),
            "url": url_for("user.profile", username=u.username),
        }
        for u in users
    ]
    return jsonify({"cards": card_hits, "users": user_hits})

