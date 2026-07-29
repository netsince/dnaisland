import base64
import random
import time
from collections import OrderedDict
from io import BytesIO

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import case, func, literal_column, or_

from ..paging import IdListPagination

from ..extensions import db
from ..models import (
    Article,
    Card,
    CardFavorite,
    CardImage,
    CardLike,
    CardTag,
    Comment,
    Punishment,
    User,
)
from ..models.teahouse import TeaPost
from ..services.card_service import attach_covers, popular_tags
from ..services.image_service import send_webp

main_bp = Blueprint("main", __name__)

# 「有图片的角色卡」在热门推荐排序中的热度加权（等效于额外互动量）。
# 用于让带封面的卡片优先被推荐，同时保留互动量极高但无图的卡片浮出空间。
IMAGE_PRIORITY_BOOST = 60


def _has_image_subquery():
    """返回「至少有一张图片」的卡片 id 子查询，用于推荐排序时给有图卡加权。"""
    return (
        db.session.query(CardImage.card_id)
        .group_by(CardImage.card_id)
        .subquery("card_has_image")
    )


@main_bp.route("/article-cover/<int:article_id>")
def article_cover(article_id):
    """文章封面：base64 类型走此端点转 WEBP；已为 WebP 的 data URL 直接发送；URL 类型由模板直接引用外链。"""
    a = db.session.get(Article, article_id)
    if not a or not a.cover:
        abort(404)
    if a.cover.startswith("data:"):
        # 上传时已转 WebP 的，直接解码发送，避免二次压缩损失
        if a.cover.startswith("data:image/webp"):
            try:
                b64 = a.cover.split(",", 1)[1]
                return send_file(
                    BytesIO(base64.b64decode(b64)),
                    mimetype="image/webp",
                    max_age=86400,
                )
            except Exception:
                pass
        return send_webp(a.cover, max_edge=1024, quality=82)
    abort(404)


def _random_recommend(limit=10):
    """首页「为你推荐」：随机推荐 limit 张，优先推荐有封面的卡。

    取全站可见卡片 id，按「是否有图片」分区，各自打乱后封面卡优先、
    无图卡补充，最终取前 limit 张。点击「换一换」时重新随机一批。
    """
    visible_ids = [r[0] for r in Card.visible_to(current_user).with_entities(Card.id).all()]
    if not visible_ids:
        return []
    has_img = set(
        r[0]
        for r in db.session.query(CardImage.card_id)
        .group_by(CardImage.card_id)
        .all()
    )
    with_img = [i for i in visible_ids if i in has_img]
    no_img = [i for i in visible_ids if i not in has_img]
    random.shuffle(with_img)
    random.shuffle(no_img)
    chosen = (with_img + no_img)[:limit]
    fetched = {c.id: c for c in Card.query.filter(Card.id.in_(chosen)).all()}
    return attach_covers([fetched[cid] for cid in chosen if cid in fetched])


@main_bp.route("/")
def index():
    # 首页「为你推荐」：随机 10 张，优先有封面的卡；点击「换一换」时 ?fragment=1
    # 仅返回卡片网格片段，由前端无刷新替换，免去整页重载与重复聚合。
    cards = _random_recommend(10)
    if request.args.get("fragment"):
        return render_template("partials/card_grid_fragment.html", cards=cards)
    return render_template(
        "index.html",
        cards=cards,
    )


def _banned_author_ids():
    """处于 profile_banned 处罚的作者，其主页不可被搜索到。"""
    return db.session.query(Punishment.user_id).filter(
        Punishment.status == "active", Punishment.type == "profile_banned"
    ).distinct()


def _likes_agg_subquery():
    """一次聚合出每张卡的赞数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            CardLike.card_id,
            func.count(CardLike.card_id).label("lc"),
        )
        .group_by(CardLike.card_id)
        .subquery("card_likes_agg")
    )


def _favorites_agg_subquery():
    """一次聚合出每张卡的收藏数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            CardFavorite.card_id,
            func.count(CardFavorite.card_id).label("fc"),
        )
        .group_by(CardFavorite.card_id)
        .subquery("card_favs_agg")
    )


def _comments_agg_subquery():
    """一次聚合出每张卡（未隐藏）评论数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            Comment.card_id,
            func.count(Comment.card_id).label("cc"),
        )
        .filter(Comment.is_hidden.is_(False))
        .group_by(Comment.card_id)
        .subquery("card_comments_agg")
    )


def _card_hot_score(interactions_expr, created_at_col):
    """Hacker News 重力时间衰减热度得分：(Interactions + 1) / (AgeInHours + 2)^1.5。"""
    if db.engine.name == "sqlite":
        age_hours = (func.julianday("now") - func.julianday(created_at_col)) * 24.0
    else:
        age_hours = func.timestampdiff(literal_column("HOUR"), created_at_col, func.now())
    return (interactions_expr + 1.0) / func.pow(age_hours + 2.0, 1.5)


def _order_by_hot(q, image_boost=IMAGE_PRIORITY_BOOST):
    """按热度排序：先 outerjoin 赞数聚合子查询，再套用时间衰减公式（消除逐行关联子查询）。

    image_boost>0 时，对「至少有一张图片」的卡片额外加权，使其优先被推荐。
    """
    agg = _likes_agg_subquery()
    q = q.outerjoin(agg, agg.c.card_id == Card.id)
    interactions = func.coalesce(Card.view_count, 0) * 1.0 + func.coalesce(agg.c.lc, 0) * 5.0
    if image_boost:
        ia = _has_image_subquery()
        q = q.outerjoin(ia, ia.c.card_id == Card.id)
        interactions = interactions + case(
            (ia.c.card_id.isnot(None), image_boost), else_=0
        )
    score = _card_hot_score(interactions, Card.created_at)
    return q.order_by(score.desc(), Card.created_at.desc())


def _order_by_home_hot(q, image_boost=IMAGE_PRIORITY_BOOST):
    """首页「热门推荐」排序：多重互动信号 + Hacker News 时间衰减。

    互动量 = 浏览×1 + 点赞×5 + 收藏×8 + 评论×3
      —— 收藏/点赞是比单纯浏览更强的正反馈信号，权重更高；评论代表讨论度。
    热度分 = (互动量 + 1) / (存在小时数 + 2)^1.5
      —— 重力模型兼顾「当下热度」与「新鲜度」：新卡凭互动快速上浮，老卡随时间自然下沉，
         避免老牌高浏览卡长期霸榜、新优质卡无法出头。
    image_boost>0 时，对「至少有一张图片」的卡片额外加权，使其优先被推荐。
    """
    la = _likes_agg_subquery()
    fa = _favorites_agg_subquery()
    ca = _comments_agg_subquery()
    q = q.outerjoin(la, la.c.card_id == Card.id)
    q = q.outerjoin(fa, fa.c.card_id == Card.id)
    q = q.outerjoin(ca, ca.c.card_id == Card.id)
    interactions = (
        func.coalesce(Card.view_count, 0) * 1.0
        + func.coalesce(la.c.lc, 0) * 5.0
        + func.coalesce(fa.c.fc, 0) * 8.0
        + func.coalesce(ca.c.cc, 0) * 3.0
    )
    if image_boost:
        ia = _has_image_subquery()
        q = q.outerjoin(ia, ia.c.card_id == Card.id)
        interactions = interactions + case(
            (ia.c.card_id.isnot(None), image_boost), else_=0
        )
    score = _card_hot_score(interactions, Card.created_at)
    return q.order_by(score.desc(), Card.created_at.desc())


def _order_by_likes(q):
    """按赞数排序：复用赞数聚合子查询，避免逐行关联子查询。"""
    agg = _likes_agg_subquery()
    q = q.outerjoin(agg, agg.c.card_id == Card.id)
    return q.order_by(func.coalesce(agg.c.lc, 0).desc(), Card.created_at.desc())


# 「热门」排序结果缓存：首页/探索默认按热度排序，每次请求都要对整表做
# 点赞/收藏/评论聚合 + Hacker News 时间衰减排序，且 paginate 还要额外 COUNT 一次。
# 这些顺序在 60s 内变化极小，故按 (路由 + 过滤条件) 缓存有序 id 列表，分页时直接切片，
# 免去每请求重算。缓存基于「全局可见」集合；登录用户屏蔽的作者可能滞后至多 TTL 出现
# 于其热门流（与 popular_tags 的取舍一致），对热门推荐流可接受。
_HOT_CARD_CACHE = OrderedDict()  # signature -> (ts, [card_id,...])
_HOT_CARD_TTL = 60


def _hot_card_order(signature, build_query, order_fn):
    """返回按热度降序排列的卡片 id 列表，带 60s TTL 缓存。"""
    now = time.time()
    hit = _HOT_CARD_CACHE.get(signature)
    if hit is not None and now - hit[0] < _HOT_CARD_TTL:
        return hit[1]
    q = order_fn(build_query())
    ids = [cid for (cid,) in q.with_entities(Card.id).all()]
    _HOT_CARD_CACHE[signature] = (now, ids)
    while len(_HOT_CARD_CACHE) > 50:
        _HOT_CARD_CACHE.popitem(last=False)
    return ids


def _paginate_hot_cards(build_query, signature, order_fn, page, per_page):
    """用缓存的有序 id 列表做分页：切片取本页 id，再按 id 批量取卡并就地排序。"""
    ids = _hot_card_order(signature, build_query, order_fn)
    total = len(ids)
    if page < 1:
        page = 1
    start = (page - 1) * per_page
    slice_ids = ids[start : start + per_page]
    cards = []
    if slice_ids:
        fetched = {c.id: c for c in Card.query.filter(Card.id.in_(slice_ids)).all()}
        cards = [fetched[cid] for cid in slice_ids if cid in fetched]
    return IdListPagination(cards, page, per_page, total)


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
        base = _order_by_hot(base)
    elif sort == "new":
        base = base.order_by(Card.created_at.desc())
    else:  # relevance
        score = case(
            (Card.name.like(like), 3),
            (CardTag.tag.like(like), 2),
            (or_(Card.intro.like(like), Card.persona.like(like)), 1),
            else_=0,
        )
        base = base.order_by(score.desc(), Card.view_count.desc(), Card.created_at.desc())
    return base


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
        TeaPost.is_deleted.is_(False),
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
        if search_type == "all":
            # 汇总视图：需三类总数 + 各取少量样例，分别 count 一次（此处无分页，故无重复计数）
            cards_count = _card_search_query(q, sort, tag).count()
            users_count = _user_search_query(q, sort).count()
            posts_count = _post_search_query(q).count()
            cards = attach_covers(_card_search_query(q, sort, tag).limit(4).all())
            users = _user_search_query(q, sort).limit(3).all()
            posts = _post_search_query(q).limit(4).all()
        elif search_type == "card":
            cards_pagination = _card_search_query(q, sort, tag).paginate(
                page=page, per_page=12, error_out=False
            )
            cards = attach_covers(cards_pagination.items)
            cards_count = cards_pagination.total  # 复用分页的 total，避免重复 COUNT
        elif search_type == "user":
            users_pagination = _user_search_query(q, sort).paginate(
                page=page, per_page=20, error_out=False
            )
            users = users_pagination.items
            users_count = users_pagination.total
        elif search_type == "post":
            posts_pagination = _post_search_query(q).paginate(
                page=page, per_page=15, error_out=False
            )
            posts = posts_pagination.items
            posts_count = posts_pagination.total

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
        # 热门排序结果缓存 60s，分页直接切片（其余排序较轻，走实时查询）。
        def _base():
            bq = Card.visible_to(current_user)
            if gender:
                bq = bq.filter(Card.gender == gender)
            if tag:
                bq = bq.join(CardTag, CardTag.card_id == Card.id).filter(
                    CardTag.tag == tag
                ).distinct()
            return bq

        signature = f"explore|g={gender or ''}|t={tag or ''}"
        pagination = _paginate_hot_cards(_base, signature, _order_by_hot, page, 24)
        cards = attach_covers(pagination.items)
    else:
        if sort == "new":
            q = q.order_by(Card.created_at.desc())
        else:  # likes：复用赞数聚合子查询，避免逐行关联子查询
            q = _order_by_likes(q)
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

