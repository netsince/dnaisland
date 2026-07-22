from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import case, func, or_
from io import BytesIO

from ..extensions import db
from ..models import Article, Card, CardLike, CardTag, Punishment, User
from ..models.teahouse import TeaPost
from ..services.card_service import attach_covers, popular_tags
from ..services.image_service import data_url_to_webp_bytes

main_bp = Blueprint("main", __name__)


@main_bp.route("/article-cover/<int:article_id>")
def article_cover(article_id):
    """文章封面：仅 base64 类型走此端点转 WEBP；URL 类型由模板直接引用外链。"""
    a = db.session.get(Article, article_id)
    if not a or not a.cover:
        abort(404)
    if a.cover.startswith("data:"):
        try:
            webp = data_url_to_webp_bytes(a.cover, max_edge=1024, quality=82)
        except Exception:
            abort(404)
        return send_file(BytesIO(webp), mimetype="image/webp", cache_timeout=86400)
    abort(404)


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


def _post_search_query(q):
    return TeaPost.query.filter(
        TeaPost.parent_id.is_(None),
        TeaPost.is_hidden.is_(False),
        TeaPost.content.ilike(f"%{q}%")
    ).order_by(TeaPost.created_at.desc())


@main_bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    search_type = request.args.get("type", "all")
    sort = request.args.get("sort", "relevance")
    tag = (request.args.get("tag") or "").strip() or None
    page = request.args.get("page", 1, type=int)

    valid_types = ("all", "card", "user", "post")
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
    posts = []
    posts_pagination = None
    cards_count = 0
    users_count = 0
    posts_count = 0

    if q:
        cards_count = _card_search_query(q, sort, tag).count()
        users_count = _user_search_query(q, sort).count()
        posts_count = _post_search_query(q).count()

        if search_type == "all":
            cards = attach_covers(_card_search_query(q, sort, tag).limit(4).all())
            users = _user_search_query(q, sort).limit(3).all()
            posts = _post_search_query(q).limit(4).all()
        elif search_type == "card":
            cards_pagination = _card_search_query(q, sort, tag).paginate(
                page=page, per_page=12, error_out=False
            )
            cards = attach_covers(cards_pagination.items)
        elif search_type == "user":
            users_pagination = _user_search_query(q, sort).paginate(
                page=page, per_page=20, error_out=False
            )
            users = users_pagination.items
        elif search_type == "post":
            posts_pagination = _post_search_query(q).paginate(
                page=page, per_page=15, error_out=False
            )
            posts = posts_pagination.items

    return render_template(
        "search.html",
        q=q,
        search_query=q,
        search_type=search_type,
        sort=sort,
        tag=tag,
        cards=cards,
        cards_pagination=cards_pagination,
        cards_count=cards_count,
        users=users,
        users_pagination=users_pagination,
        users_count=users_count,
        posts=posts,
        posts_pagination=posts_pagination,
        posts_count=posts_count,
        args=args,
    )


@main_bp.route("/explore")
def explore():
    page = request.args.get("page", 1, type=int)
    gender = (request.args.get("gender") or "").strip()
    tag = (request.args.get("tag") or "").strip() or None
    sort = request.args.get("sort", "hot")
    if sort not in ("hot", "new", "likes"):
        sort = "hot"

    q = Card.visible_to(current_user)
    if gender:
        q = q.filter(Card.gender == gender)
    if tag:
        q = q.join(CardTag, CardTag.card_id == Card.id).filter(CardTag.tag == tag)

    if sort == "hot":
        q = q.order_by(Card.view_count.desc(), Card.created_at.desc())
    elif sort == "new":
        q = q.order_by(Card.created_at.desc())
    else:  # likes：相关子查询计数，避免 group by 触发 only_full_group_by 问题
        like_count = (
            db.session.query(func.count(CardLike.card_id))
            .filter(CardLike.card_id == Card.id)
            .correlate(Card)
            .scalar_subquery()
        )
        q = q.order_by(like_count.desc(), Card.created_at.desc())

    if tag:
        q = q.distinct()

    pagination = q.paginate(page=page, per_page=24, error_out=False)
    cards = attach_covers(pagination.items)

    genders = [
        g[0]
        for g in (
            Card.visible_to(current_user)
            .with_entities(Card.gender)
            .filter(Card.gender.is_not(None), Card.gender != "")
            .distinct()
            .all()
        )
    ]
    tags = popular_tags(current_user, limit=30)

    # 分页链接需保留当前筛选条件
    args = {"sort": sort}
    if gender:
        args["gender"] = gender
    if tag:
        args["tag"] = tag

    return render_template(
        "explore.html",
        cards=cards,
        pagination=pagination,
        genders=genders,
        tags=tags,
        gender=gender,
        tag=tag,
        sort=sort,
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


# ---------------- 法律协议（前台公开页，供系统配置中的协议链接使用） ----------------
@main_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@main_bp.route("/tos")
def tos():
    return render_template("legal/tos.html")


# ---------------- 文章（前台） ----------------
@main_bp.route("/articles")
def articles():
    page = request.args.get("page", 1, type=int)
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "new")

    # 排序：最新（默认）/ 最早 / 最近更新
    if sort == "old":
        order = Article.created_at.asc()
    elif sort == "updated":
        order = Article.updated_at.desc()
    else:
        sort = "new"
        order = Article.created_at.desc()

    try:
        query = Article.query.filter_by(is_published=True)
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Article.title.like(like),
                    Article.summary.like(like),
                    Article.content.like(like),
                )
            )
        pagination = query.order_by(order).paginate(
            page=page, per_page=10, error_out=False
        )
        items = pagination.items
    except Exception:
        # 表尚未建立（如迁移未执行）时优雅降级为空列表
        pagination = None
        items = []

    # 翻页链接需保留当前搜索词与排序
    args = {}
    if q:
        args["q"] = q
    if sort != "new":
        args["sort"] = sort
    return render_template(
        "articles/index.html",
        articles=items,
        pagination=pagination,
        args=args,
        q=q,
        sort=sort,
    )


@main_bp.route("/articles/<int:article_id>")
def article_detail(article_id):
    try:
        a = db.session.get(Article, article_id)
    except Exception:
        a = None
    if a is None or not a.is_published:
        abort(404)
    return render_template("articles/show.html", article=a)

