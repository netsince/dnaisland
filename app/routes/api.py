"""App 端统一的 JSON API 蓝图（/api/v1）。

为 Flutter App 提供全套 JSON 接口，复用现有模型与服务：
  - Token 认证（JWT，与 flask_login Session 共存）
  - 所有返回格式统一为 {"ok": bool, "data": ..., "error": str}
  - 分页统一为 {"items": [...], "page": int, "pages": int, "total": int, "has_next": bool}
"""

import functools
import time
from datetime import datetime

import jwt
from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_

from ..extensions import db
from ..models import (
    Card,
    CardCopyStat,
    CardDialogueStyle,
    CardFavorite,
    CardImage,
    CardLike,
    CardTag,
    Comment,
    CommentLike,
    Notification,
    PointTransaction,
    SiteRecommendation,
    TeaPost,
    TeaPostFavorite,
    TeaPostImage,
    TeaPostLike,
    TeaPostTopic,
    TeaTopic,
    User,
    UserFollow,
)
from ..routes.main import featured_cards
from ..routes.teahouse import _visible_query as _teahouse_visible_query
from ..services.card_service import build_export_package, enrich_cards
from ..routes.card_lists import (
    card_comments_list,
    card_detail_core,
    card_export_package,
    create_comment,
    explore_cards,
    profile_cards,
    recommend_items,
    search_cards,
)
from ..routes.card_lists import my_cards as _shared_my_cards
from ..routes.card_lists import my_favorites as _shared_my_favorites
from ..routes.card_lists import my_likes as _shared_my_likes
from ..services.notification_service import mark_all_read, unread_count
from ..utils import get_user_by_username, toggle_relation

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# ---------------------------------------------------------------------------
# JWT 工具
# ---------------------------------------------------------------------------

def _jwt_secret():
    return current_app.config["SECRET_KEY"]


def _make_token(user_id: int, *, expire_hours=168) -> str:
    """签发 JWT，默认 7 天过期。"""
    payload = {
        "user_id": user_id,
        "exp": int(time.time()) + expire_hours * 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def api_login_required(fn):
    """API 专用登录校验装饰器：优先检查 Authorization: Bearer <token>，
    回退到 flask_login session（兼容网页端登录用户）。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            payload = _decode_token(auth[7:])
            if payload is None:
                return jsonify(ok=False, error="登录已过期，请重新登录"), 401
            user = db.session.get(User, payload["user_id"])
            if not user or user.is_locked:
                return jsonify(ok=False, error="账号异常，无法操作"), 403
            g.api_user = user
        elif current_user.is_authenticated:
            g.api_user = current_user
        else:
            return jsonify(ok=False, error="请先登录"), 401
        return fn(*args, **kwargs)
    return wrapper


def _ensure_self():
    """API 上下文里获取当前用户。"""
    return getattr(g, "api_user", current_user)


# ---------------------------------------------------------------------------
# 统一响应辅助
# ---------------------------------------------------------------------------

def ok(data=None):
    return jsonify(ok=True, data=data)


def err(msg, code=400):
    return jsonify(ok=False, error=msg), code


def paginated(query, page=1, per_page=20, *, serialize_fn=None):
    """通用分页响应。"""
    if page < 1:
        page = 1
    pag = query.paginate(page=page, per_page=per_page, error_out=False)
    items = [serialize_fn(item) for item in pag.items] if serialize_fn else pag.items
    return jsonify(ok=True, data={
        "items": items,
        "page": pag.page,
        "pages": pag.pages,
        "total": pag.total,
        "has_next": pag.has_next,
    })


def _cards_response(pag, cards):
    """把共享函数返回的 (pagination, cards) 序列化成 App JSON。"""
    return jsonify(ok=True, data={
        "items": [_card_light(c) for c in cards],
        "page": pag.page,
        "pages": pag.pages,
        "total": pag.total,
        "has_next": pag.has_next,
    })


# ---------------------------------------------------------------------------
# 序列化器（模型 → dict）
# ---------------------------------------------------------------------------

def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.display_name,
        "avatar": user.avatar or "",
        "bio": user.bio or "",
        "location": user.location or "",
        "website": user.website or "",
        "verified": bool(user.verified),
        "verified_label": user.verified_label or "",
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }


def _card_light(card: Card) -> dict:
    """轻量卡片摘要（卡片列表通用）。

    封面与作者已由 card_service.enrich_cards 批量预载（card.covers / card.author），
    这里不再逐卡发 count/查询，避免 N+1，使 App 端与网页版同样快。
    """
    return {
        "id": card.id,
        "name": card.name,
        "gender": card.gender,
        "intro": (card.intro or "")[:200],
        "view_count": card.view_count or 0,
        "copy_count": card.copy_count or 0,
        "covers": getattr(card, "covers", {}) or {},
        "created_at": card.created_at.isoformat() if card.created_at else "",
        "author": _user_public(card.author) if card.author else None,  # type: ignore[arg-type]
    }


def _card_detail(card: Card, data: dict) -> dict:
    """卡片详情 JSON（data 由 card_detail_core 一次取好，无重复查询）。"""
    return {
        "id": card.id,
        "name": card.name,
        "gender": card.gender,
        "persona": card.persona or "",
        "intro": card.intro or "",
        "opening": card.opening or "",
        "original_link": card.original_link or "",
        "view_count": card.view_count or 0,
        "copy_count": card.copy_count or 0,
        "like_count": data["like_count"],
        "favorite_count": data["favorite_count"],
        "comment_count": data["comment_count"],
        "liked": data["liked"],
        "favorited": data["favorited"],
        "tags": data["tags"],
        "dialogue": data["dialogue"],
        "images": data["images"],
        "cover_focus": card.cover_focus or "",
        "created_at": card.created_at.isoformat() if card.created_at else "",
        "updated_at": card.updated_at.isoformat() if card.updated_at else "",
        "status": card.status,
        "author": _user_public(card.author) if card.author else None,  # type: ignore[arg-type]
    }


def _comment_item(cm, liked_by_user: bool, like_count: int) -> dict:
    return {
        "id": cm.id,
        "content": cm.content,
        "has_image": bool(cm.image_data),
        "created_at": cm.created_at.isoformat() if cm.created_at else "",
        "author": _user_public(cm.author) if cm.author else None,
        "reply_to": {
            "id": cm.reply_to.id,
            "author_name": cm.reply_to.author.display_name if (cm.reply_to and cm.reply_to.author) else "未知用户",
        } if cm.reply_to else None,
        "like_count": like_count,
        "liked": liked_by_user,
        "is_pinned": bool(cm.is_pinned),
    }


def _teapost_item(post: TeaPost, stats: dict) -> dict:
    """茶馆帖子摘要。"""
    img = post.images[0] if post.images else None  # type: ignore[index]
    return {
        "id": post.id,
        "content": post.content,
        "created_at": post.created_at.isoformat() if post.created_at else "",
        "author": _user_public(post.author) if post.author else None,  # type: ignore[arg-type]
        "image": {
            "id": img.id,
            "sort_order": img.sort_order,
            "url": f"/api/v1/teahouse/images/{img.id}",
        } if img else None,
        "card": {
            "id": post.card.id,
            "name": post.card.name,
        } if post.card else None,
        "parent_id": post.parent_id,
        "is_deleted": post.is_deleted,
        "is_hidden": post.is_hidden,
        "stats": stats,
    }


def _notification_item(n: Notification) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "message": n.message,
        "related_card_id": n.related_card_id,
        "is_read": bool(n.is_read),
        "created_at": n.created_at.isoformat() if n.created_at else "",
    }


def _point_tx(tx: PointTransaction) -> dict:
    return {
        "id": tx.id,
        "delta": tx.delta,
        "balance_after": tx.balance_after,
        "reason": tx.reason,
        "source": tx.source,
        "created_at": tx.created_at.isoformat() if tx.created_at else "",
    }


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

@api_bp.route("/auth/token", methods=["POST"])
def auth_token():
    """JWT 登录：用用户名/邮箱 + 密码换 token。"""
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    password = data.get("password") or ""
    if not identifier or not password:
        return err("请提供用户名/邮箱和密码")

    user = User.query.filter(
        or_(User.username == identifier, User.email == identifier)
    ).first()
    if not user or not user.check_password(password):
        return err("用户名/邮箱或密码错误", 401)
    if user.is_locked:
        return err("该账号已被封禁或注销，无法登录", 403)

    # JWT 签发：记登录，但不影响 flask_login session
    token = _make_token(user.id)
    return ok({
        "token": token,
        "user": _user_public(user),
    })


@api_bp.route("/auth/refresh", methods=["POST"])
@api_login_required
def auth_refresh():
    """刷新 JWT。"""
    user = _ensure_self()
    token = _make_token(user.id)
    return ok({"token": token})


@api_bp.route("/auth/me", methods=["GET"])
@api_login_required
def auth_me():
    """当前用户信息。"""
    user = _ensure_self()
    return ok(_user_public(user))


# ---------------------------------------------------------------------------
# 角色卡
# ---------------------------------------------------------------------------

@api_bp.route("/cards/featured", methods=["GET"])
def cards_featured():
    """首页推荐：与网页版共用 featured_cards 统一入口，加 exclude 去重。

    用轻量序列化 _card_light（封面/作者已由 featured_cards 批量预载），
    不再逐卡统计，消除 N+1，App 端与网页版同样快。
    """
    exclude_raw = request.args.get("exclude", "")
    exclude_ids = exclude_raw.split(",") if exclude_raw else None
    cards = featured_cards(12, exclude_ids)
    return ok([_card_light(c) for c in cards])


@api_bp.route("/recommend", methods=["GET"])
def recommend():
    """站长推荐：与网页版共用 recommend_items 一个函数。"""
    rec_items = recommend_items()
    items = []
    for it in rec_items:
        if it["kind"] == "card":
            items.append(
                {"kind": "card", "note": it["note"], "data": _card_light(it["card"])}
            )
        else:
            items.append(
                {
                    "kind": "user",
                    "note": it["note"],
                    "card_count": it["card_count"],
                    "data": _user_public(it["user"]),
                }
            )
    return ok({"items": items})


@api_bp.route("/cards/<card_id>/export", methods=["GET"])
@api_login_required
def cards_export(card_id):
    """复制角色卡：与网页版共用 card_export_package 一个函数。"""
    user = _ensure_self()
    card, package, err_code = card_export_package(card_id, user)
    if err_code == "unauth":
        return err("请先登录后再复制角色卡", 401)
    if err_code in ("not_found", "forbidden"):
        return err("角色卡不存在", 404)
    return ok({"package": package, "copy_count": card.copy_count or 0})


@api_bp.route("/cards/explore", methods=["GET"])
def cards_explore():
    """探索分页：与网页版共用 explore_cards 一个函数。"""
    page = request.args.get("page", 1, type=int)
    gender = (request.args.get("gender") or "").strip()
    tag = (request.args.get("tag") or "").strip() or None
    sort = request.args.get("sort", "hot")
    pag, cards = explore_cards(
        _ensure_self(), page=page, gender=gender, tag=tag, sort=sort, per_page=24
    )
    return _cards_response(pag, cards)


@api_bp.route("/cards/search", methods=["GET"])
def cards_search():
    """搜索角色卡：与网页版共用 search_cards 一个函数。"""
    q = (request.args.get("q") or "").strip()
    if not q:
        return err("请提供搜索词")
    sort = request.args.get("sort", "relevance")
    tag = (request.args.get("tag") or "").strip() or None
    page = request.args.get("page", 1, type=int)
    pag, cards = search_cards(
        _ensure_self(), q, sort=sort, tag=tag, page=page, per_page=12
    )
    return _cards_response(pag, cards)


@api_bp.route("/cards/<card_id>", methods=["GET"])
def cards_detail(card_id):
    """角色卡详情：与网页版共用 card_detail_core 一个函数。"""
    card, data, err_code = card_detail_core(card_id, _ensure_self())
    if err_code in ("not_found", "forbidden"):
        return err("角色卡不存在", 404)
    return ok(_card_detail(card, data))


# ---------------------------------------------------------------------------
# 角色卡写操作（点赞/收藏/评论等，复用现有 XHR 逻辑）
# ---------------------------------------------------------------------------

@api_bp.route("/cards/<card_id>/like", methods=["POST"])
@api_login_required
def cards_like(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        return err("角色卡不存在", 404)
    now_active, count = toggle_relation(
        CardLike.query.filter_by(user_id=_ensure_self().id, card_id=card_id).first(),
        CardLike(user_id=_ensure_self().id, card_id=card_id),
        CardLike.query.filter_by(card_id=card_id),
    )
    return ok({"liked": now_active, "count": count})


@api_bp.route("/cards/<card_id>/favorite", methods=["POST"])
@api_login_required
def cards_favorite(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        return err("角色卡不存在", 404)
    now_active, count = toggle_relation(
        CardFavorite.query.filter_by(user_id=_ensure_self().id, card_id=card_id).first(),
        CardFavorite(user_id=_ensure_self().id, card_id=card_id),
        CardFavorite.query.filter_by(card_id=card_id),
    )
    return ok({"favorited": now_active, "count": count})


@api_bp.route("/cards/<card_id>/comments", methods=["GET"])
def cards_comments(card_id):
    """角色卡评论列表：与网页版共用 card_comments_list 一个函数。"""
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "latest")
    card, data, err_code = card_comments_list(
        card_id, _ensure_self(), page=page, per_page=20, sort=sort
    )
    if err_code == "not_found":
        return err("角色卡不存在", 404)
    pag = data["pagination"]
    like_counts = data["like_counts"]
    user_liked_ids = data["user_liked_ids"]
    items = [
        _comment_item(cm, cm.id in user_liked_ids, like_counts.get(cm.id, 0))
        for cm in pag.items
    ]
    return ok({
        "items": items,
        "page": pag.page,
        "pages": pag.pages,
        "total": pag.total,
        "has_next": pag.has_next,
    })


@api_bp.route("/cards/<card_id>/comments", methods=["POST"])
@api_login_required
def cards_comment_post(card_id):
    """发表评论：与网页版共用 create_comment 一个函数。"""
    card = db.session.get(Card, card_id)
    if not card:
        return err("角色卡不存在", 404)

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    raw_reply = data.get("reply_to_id")
    reply_to_id = int(raw_reply) if str(raw_reply).isdigit() else None
    image_data = data.get("image_data") or None

    cm, err_code = create_comment(
        card_id, _ensure_self(), content,
        reply_to_id=reply_to_id, image_data=image_data,
    )
    if err_code == "not_found":
        return err("角色卡不存在", 404)
    if err_code == "muted":
        return err("你已被禁言，暂时无法评论", 403)
    if err_code == "empty":
        return err("评论内容不能为空")
    if err_code == "too_long":
        return err("评论不能超过 500 字")
    return ok({"id": cm.id, "message": "评论成功"}), 201


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

@api_bp.route("/users/<username>", methods=["GET"])
def users_profile(username):
    """用户主页。"""
    u = get_user_by_username(username)
    if not u:
        return err("用户不存在", 404)
    cu = _ensure_self()
    is_self = cu.is_authenticated and cu.id == u.id
    is_admin = cu.is_authenticated and cu.is_super_admin
    restricted = (not is_self) and (not is_admin) and u.is_profile_banned
    if restricted:
        return ok({**(_user_public(u)), "restricted": True})

    # 用户卡片列表：与网页版共用 profile_cards 一个函数（含可见性/隐私过滤）。
    page = request.args.get("page", 1, type=int)
    _u, cards_pag, cards = profile_cards(cu, username, page=page, per_page=12)

    follower_count = UserFollow.query.filter_by(following_id=u.id).count()
    following_count = UserFollow.query.filter_by(follower_id=u.id).count()
    is_following = (
        cu.is_authenticated
        and UserFollow.query.filter_by(follower_id=cu.id, following_id=u.id).first() is not None
    )

    return ok({
        "user": _user_public(u),
        "restricted": False,
        "follower_count": follower_count,
        "following_count": following_count,
        "is_following": is_following,
        "cards": {
            "items": [_card_light(c) for c in cards],
            "page": cards_pag.page,
            "pages": cards_pag.pages,
            "total": cards_pag.total,
        },
    })


@api_bp.route("/users/<username>/followers", methods=["GET"])
@api_bp.route("/users/<username>/following", methods=["GET"])
def users_follow_list(username):
    """某用户的粉丝/关注列表（分页 JSON），按关注时间倒序。

    items 中每个用户附带 is_following（当前登录用户是否已关注对方）。
    他人访问时过滤掉「禁止主页被访问」的用户。
    """
    u = get_user_by_username(username)
    if not u:
        return err("用户不存在", 404)
    cu = _ensure_self()
    is_self = cu.is_authenticated and cu.id == u.id
    is_admin = cu.is_authenticated and cu.is_super_admin
    kind = "followers" if request.path.rstrip("/").endswith("/followers") else "following"

    page = request.args.get("page", 1, type=int)
    if kind == "followers":
        link_col = UserFollow.following_id  # 被关注者 = u，展示粉丝
        join_col = UserFollow.follower_id
    else:
        link_col = UserFollow.follower_id  # 关注者 = u，展示已关注的人
        join_col = UserFollow.following_id
    pag = (
        db.session.query(User)
        .join(UserFollow, join_col == User.id)
        .filter(link_col == u.id)
        .order_by(UserFollow.created_at.desc())
        .paginate(page=page, per_page=20, error_out=False)
    )

    items = list(pag.items)
    if not (is_self or is_admin):
        items = [x for x in items if not x.is_profile_banned]

    ids = [x.id for x in items]
    following_ids: set = set()
    if cu.is_authenticated and ids:
        rows = UserFollow.query.filter(
            UserFollow.follower_id == cu.id,
            UserFollow.following_id.in_(ids),
        ).all()
        following_ids = {r.following_id for r in rows}

    return ok({
        "items": [
            {**_user_public(x), "is_following": x.id in following_ids}
            for x in items
        ],
        "page": pag.page,
        "pages": pag.pages,
        "total": pag.total,
        "has_next": pag.has_next,
    })


@api_bp.route("/users/<username>/follow", methods=["POST"])
@api_login_required
def users_follow(username):
    target = get_user_by_username(username)
    if not target or target.is_profile_banned:
        return err("用户不存在", 404)
    cu = _ensure_self()
    if target.id == cu.id:
        return err("不能关注自己")
    now_following, _ = toggle_relation(
        UserFollow.query.filter_by(follower_id=cu.id, following_id=target.id).first(),
        UserFollow(follower_id=cu.id, following_id=target.id),
        UserFollow.query.filter_by(following_id=target.id),
    )
    if now_following:
        from ..services.notification_service import notify
        notify(target.id, f"{cu.nickname} 关注了你", type_="follow")
    db.session.commit()
    return ok({"following": bool(now_following)})


# ---------------------------------------------------------------------------
# 我的
# ---------------------------------------------------------------------------

@api_bp.route("/my/cards", methods=["GET"])
@api_login_required
def my_cards():
    page = request.args.get("page", 1, type=int)
    pag, cards = _shared_my_cards(_ensure_self(), page=page, per_page=12)
    return _cards_response(pag, cards)


@api_bp.route("/my/favorites", methods=["GET"])
@api_login_required
def my_favorites():
    page = request.args.get("page", 1, type=int)
    pag, cards = _shared_my_favorites(_ensure_self(), page=page, per_page=12)
    return _cards_response(pag, cards)


@api_bp.route("/my/likes", methods=["GET"])
@api_login_required
def my_likes():
    page = request.args.get("page", 1, type=int)
    pag, cards = _shared_my_likes(_ensure_self(), page=page, per_page=12)
    return _cards_response(pag, cards)


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------

@api_bp.route("/notifications", methods=["GET"])
@api_login_required
def notifications():
    page = request.args.get("page", 1, type=int)
    q = Notification.query.filter_by(user_id=_ensure_self().id).order_by(Notification.created_at.desc())
    result = paginated(q, page=page, per_page=20, serialize_fn=_notification_item)
    # 附加未读计数
    data = result.json.get("data", {})
    data["unread_count"] = unread_count(_ensure_self().id)
    return jsonify(ok=True, data=data)


@api_bp.route("/notifications/read-all", methods=["POST"])
@api_login_required
def notifications_read_all():
    mark_all_read(_ensure_self().id)
    return ok({"message": "已全部标记为已读"})


@api_bp.route("/notifications/unread-count", methods=["GET"])
@api_login_required
def notifications_unread_count():
    return ok({"unread_count": unread_count(_ensure_self().id)})


# ---------------------------------------------------------------------------
# 茶馆
# ---------------------------------------------------------------------------

@api_bp.route("/teahouse/posts", methods=["GET"])
def teahouse_posts():
    """茶馆 Feed：sort=hot|new|random, page=1, topic_id=可选。"""
    from ..routes.teahouse import _build_stats, _hot_post_order

    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "new")
    topic_id = request.args.get("topic_id", type=int)
    per_page = 20

    q = TeaPost.query.filter(TeaPost.parent_id.is_(None))
    q = _teahouse_visible_query(q, _ensure_self())

    if topic_id:
        sub = db.session.query(TeaPostTopic.post_id).filter_by(topic_id=topic_id)
        q = q.filter(TeaPost.id.in_(sub))

    if sort == "hot":
        sig = "api_teahouse_hot"
        ids = _hot_post_order(sig, lambda: _teahouse_visible_query(
            TeaPost.query.filter(TeaPost.parent_id.is_(None)), _ensure_self()
        ))
        if page < 1:
            page = 1
        start = (page - 1) * per_page
        slice_ids = ids[start: start + per_page]
        posts = []
        if slice_ids:
            fetched = {
                p.id: p for p in _teahouse_visible_query(
                    TeaPost.query.filter(TeaPost.id.in_(slice_ids)), _ensure_self()
                ).all()
            }
            posts = [fetched[pid] for pid in slice_ids if pid in fetched]
        total = len(ids)
        pages = max(1, (total + per_page - 1) // per_page)
        stats = _build_stats(posts)
        items = [_teapost_item(p, stats.get(p.id, {})) for p in posts]
        return ok({"items": items, "page": page, "pages": pages, "total": total, "has_next": page < pages})
    else:
        q = q.order_by(TeaPost.created_at.desc())
        pag = q.paginate(page=page, per_page=per_page, error_out=False)
        stats = _build_stats(pag.items)
        items = [_teapost_item(p, stats.get(p.id, {})) for p in pag.items]
        return ok({
            "items": items,
            "page": pag.page,
            "pages": pag.pages,
            "total": pag.total,
            "has_next": pag.has_next,
        })


@api_bp.route("/teahouse/posts", methods=["POST"])
@api_login_required
def teahouse_create_post():
    from ..routes.teahouse import (
        TEA_POST_MAX_LEN,
        _notify_mentions,
        _resolve_card,
        _set_single_topic,
        _too_frequent,
    )
    from ..services.sticker_service import sanitize_stickers

    if getattr(_ensure_self(), "is_muted", False):
        return err("你已被禁言", 403)

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        return err("内容不能为空")
    if len(content) > TEA_POST_MAX_LEN:
        return err(f"内容不能超过 {TEA_POST_MAX_LEN} 字")

    if _too_frequent(_ensure_self().id):
        return err("发帖太频繁了，请稍后再试", 429)

    post = TeaPost(user_id=_ensure_self().id, content=content)
    card_id = data.get("card_id")
    card = _resolve_card(card_id, _ensure_self())
    if card:
        post.card_id = card.id

    db.session.add(post)
    db.session.flush()

    # 配图（base64 data URL，与网页端一致仅取首图）
    images = data.get("images") or []
    if images:
        if not isinstance(images, list):
            images = [images]
        first = images[0]
        try:
            from ..services.image_service import data_url_to_bytes_and_mime
            data_url_to_bytes_and_mime(first)
        except Exception:
            return err("图片格式非法")
        db.session.add(TeaPostImage(post_id=post.id, image_data=first, sort_order=0))

    # 话题
    topic_raw = data.get("topic")
    if topic_raw:
        _set_single_topic(post, topic_raw)
    db.session.commit()
    _notify_mentions(content, post, _ensure_self())
    return ok({"id": post.id, "message": "发布成功"}), 201


@api_bp.route("/teahouse/posts/<int:post_id>", methods=["GET"])
def teahouse_post_detail(post_id):
    from ..routes.teahouse import _build_stats, _order_by_teahouse_hot, _require_visible_post

    p = _require_visible_post(post_id)
    chain = []
    node = p.parent
    while node is not None:
        if not node.is_deleted:
            chain.append(node)
        node = node.parent
    chain.reverse()
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "hot")

    rq = TeaPost.query.filter(TeaPost.parent_id == p.id)
    rq = _teahouse_visible_query(rq, _ensure_self())
    rq = _order_by_teahouse_hot(rq) if sort == "hot" else rq.order_by(TeaPost.created_at.desc())
    reply_pag = rq.paginate(page=page, per_page=20, error_out=False)

    stats = _build_stats(list(dict.fromkeys([p] + reply_pag.items + chain)))

    return ok({
        "post": _teapost_item(p, stats.get(p.id, {})),
        "chain": [_teapost_item(c, stats.get(c.id, {})) for c in chain],
        "replies": {
            "items": [_teapost_item(r, stats.get(r.id, {})) for r in reply_pag.items],
            "page": reply_pag.page,
            "pages": reply_pag.pages,
            "total": reply_pag.total,
            "has_next": reply_pag.has_next,
        },
    })


@api_bp.route("/teahouse/posts/<int:post_id>/reply", methods=["POST"])
@api_login_required
def teahouse_reply(post_id):
    from ..routes.teahouse import (
        TEA_POST_MAX_LEN,
        _notify_mentions,
        _require_visible_post,
        _resolve_card,
        _set_single_topic,
        _too_frequent,
    )
    from ..services.notification_service import notify
    from ..services.sticker_service import sanitize_stickers

    if getattr(_ensure_self(), "is_muted", False):
        return err("你已被禁言", 403)

    parent = _require_visible_post(post_id)
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        return err("回复内容不能为空")
    if len(content) > TEA_POST_MAX_LEN:
        return err(f"内容不能超过 {TEA_POST_MAX_LEN} 字")
    if _too_frequent(_ensure_self().id):
        return err("回复太频繁了，请稍后再试", 429)

    reply = TeaPost(user_id=_ensure_self().id, parent_id=post_id, content=content)
    card_id = data.get("card_id")
    card = _resolve_card(card_id, _ensure_self())
    if card:
        reply.card_id = card.id
    db.session.add(reply)
    db.session.flush()

    topic_raw = data.get("topic")
    if topic_raw:
        _set_single_topic(reply, topic_raw)
    db.session.commit()

    if parent.user_id != _ensure_self().id:
        notify(parent.user_id, f"{_ensure_self().nickname} 回复了你的茶馆帖子：{content[:30]}", type_="teahouse")
    _notify_mentions(content, reply, _ensure_self())

    return ok({"id": reply.id, "message": "回复成功"}), 201


@api_bp.route("/teahouse/posts/<int:post_id>/like", methods=["POST"])
@api_login_required
def teahouse_like(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p or (p.is_deleted and not _ensure_self().is_super_admin):
        return err("帖子不存在", 404)
    now_liked, count = toggle_relation(
        TeaPostLike.query.filter_by(user_id=_ensure_self().id, post_id=post_id).first(),
        TeaPostLike(user_id=_ensure_self().id, post_id=post_id),
        TeaPostLike.query.filter_by(post_id=post_id),
    )
    # 通知（简化版本）
    if now_liked and p.user_id != _ensure_self().id:
        from ..services.notification_service import notify
        dup = Notification.query.filter_by(user_id=p.user_id, type="like", is_read=False)\
            .filter(Notification.message.contains(f"/teahouse/{p.id}")).first()
        if not dup:
            notify(p.user_id, f"{_ensure_self().nickname} 赞了你在茶馆的帖子", type_="like")
    db.session.commit()
    return ok({"liked": now_liked, "count": count})


@api_bp.route("/teahouse/posts/<int:post_id>/favorite", methods=["POST"])
@api_login_required
def teahouse_favorite(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p or (p.is_deleted and not _ensure_self().is_super_admin):
        return err("帖子不存在", 404)
    now_fav, count = toggle_relation(
        TeaPostFavorite.query.filter_by(user_id=_ensure_self().id, post_id=post_id).first(),
        TeaPostFavorite(user_id=_ensure_self().id, post_id=post_id),
        TeaPostFavorite.query.filter_by(post_id=post_id),
    )
    db.session.commit()
    return ok({"favorited": now_fav, "count": count})


@api_bp.route("/teahouse/topics", methods=["GET"])
def teahouse_topics():
    """热门话题列表。"""
    topics = TeaTopic.query.order_by(TeaTopic.post_count.desc()).limit(30).all()
    return ok([{"id": t.id, "name": t.name, "post_count": t.post_count} for t in topics])


# ---------------------------------------------------------------------------
# 积分
# ---------------------------------------------------------------------------

@api_bp.route("/points", methods=["GET"])
@api_login_required
def points():
    page = request.args.get("page", 1, type=int)
    q = PointTransaction.query.filter_by(user_id=_ensure_self().id).order_by(PointTransaction.created_at.desc())
    result = paginated(q, page=page, per_page=20, serialize_fn=_point_tx)
    data = result.json.get("data", {})
    data["balance"] = _ensure_self().points or 0
    return jsonify(ok=True, data=data)


@api_bp.route("/points/redeem", methods=["POST"])
@api_login_required
def points_redeem():
    from ..models import KeyUsageLog, RedemptionKey
    from ..routes.points import MAX_KEYS_PER_REQUEST, record_redeem, redeem_allowed

    data = request.get_json(silent=True) or {}
    codes_raw = data.get("codes")
    if not codes_raw or not isinstance(codes_raw, list):
        return err("请提供兑换码列表")
    codes = list(dict.fromkeys(c.strip() for c in codes_raw if c.strip()))
    if not codes:
        return err("请提供至少一个兑换码")
    if len(codes) > MAX_KEYS_PER_REQUEST:
        return err(f"一次最多兑换 {MAX_KEYS_PER_REQUEST} 个")

    ok_flag, msg = redeem_allowed(_ensure_self().id)
    if not ok_flag:
        return err(msg)

    results = []
    success_count = 0
    for code in codes:
        key = RedemptionKey.query.filter_by(code=code).first()
        if not key:
            results.append({"code": code, "ok": False, "message": "兑换码不存在"})
            db.session.add(KeyUsageLog(code=code, user_id=_ensure_self().id, status="fail", note="兑换码不存在"))
            continue
        if not key.active:
            results.append({"code": code, "ok": False, "message": "兑换码已被禁用"})
            continue
        if not key.is_valid_now():
            results.append({"code": code, "ok": False, "message": "兑换码不在有效期内"})
            continue
        if key.used_count >= key.max_uses:
            results.append({"code": code, "ok": False, "message": "已达使用上限"})
            continue
        used_by_user = KeyUsageLog.query.filter_by(key_id=key.id, user_id=_ensure_self().id, status="success").count()
        if used_by_user >= key.per_user_limit:
            results.append({"code": code, "ok": False, "message": "你已使用过该兑换码"})
            continue

        _ensure_self().points = (_ensure_self().points or 0) + key.points
        key.used_count += 1
        db.session.add(PointTransaction(
            user_id=_ensure_self().id, delta=key.points,
            balance_after=_ensure_self().points,
            reason=f"兑换码 {code}", source="redeem", related_key=code,
        ))
        db.session.add(KeyUsageLog(key_id=key.id, code=code, user_id=_ensure_self().id, points_gained=key.points, status="success"))
        results.append({"code": code, "ok": True, "message": f"+{key.points} 点数"})
        success_count += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return err("兑换失败，请稍后重试")

    record_redeem(_ensure_self().id, success_count > 0)
    return ok({"results": results, "success_count": success_count, "fail_count": len(codes) - success_count})


# ---------------------------------------------------------------------------
# 系统信息
# ---------------------------------------------------------------------------

@api_bp.route("/site-config", methods=["GET"])
def site_config():
    """站点公开配置（复用现有 /api/site-config）。"""
    from ..services.site_service import public_config
    return jsonify(ok=True, data=public_config())


# ---------------------------------------------------------------------------
# 图片上传（客户端 multipart 文件 → WebP data URL）
# ---------------------------------------------------------------------------

@api_bp.route("/upload/image", methods=["POST"])
@api_login_required
def upload_image():
    """通用图片上传。接收 multipart 文件，压缩为 WebP data URL 返回，
    客户端可在发茶馆帖/评论时引用返回的图片串。

    表单字段：
      - image: 图片文件（必填）
      - kind:  teapost（默认，1280px）/ comment（1024px），影响压缩尺寸
    返回：{"image": "<data url>", "bytes": int}
    """
    from ..services.image_service import raw_bytes_to_webp_data_url

    f = request.files.get("image")
    if not f or not f.filename:
        return err("未收到图片文件")
    raw = f.read()
    if not raw:
        return err("图片内容为空")
    if len(raw) > 16 * 1024 * 1024:
        return err("图片过大，请控制在 16MB 以内")

    kind = (request.form.get("kind") or "teapost").lower()
    max_edge, quality = (1024, 80) if kind == "comment" else (1280, 82)
    try:
        data_url = raw_bytes_to_webp_data_url(raw, max_edge=max_edge, quality=quality)
    except Exception:
        return err("图片格式无法识别或处理失败")
    return ok({"image": data_url, "bytes": len(data_url)})


@api_bp.route("/me/avatar", methods=["POST"])
@api_login_required
def me_avatar():
    """上传并更新头像。接收 multipart 文件，居中裁剪为 256x256 WebP。"""
    from ..services.image_service import crop_square_and_compress_bytes

    user = _ensure_self()
    if getattr(user, "is_edit_profile_banned", False):
        return err("当前账号被禁止修改资料", 403)
    f = request.files.get("image")
    if not f or not f.filename:
        return err("未收到图片文件")
    raw = f.read()
    if not raw:
        return err("图片内容为空")
    if len(raw) > 16 * 1024 * 1024:
        return err("图片过大，请控制在 16MB 以内")
    try:
        data_url = crop_square_and_compress_bytes(raw, size=256, quality=82)
    except Exception:
        return err("图片格式无法识别或处理失败")
    user.avatar = data_url
    db.session.commit()
    return ok({"avatar": data_url})


@api_bp.route("/teahouse/images/<int:image_id>", methods=["GET"])
def teahouse_image(image_id):
    """按 id 返回茶馆配图（WebP）。供客户端显示上传后的图片。"""
    from ..services.image_service import send_webp

    img = db.session.get(TeaPostImage, image_id)
    if not img:
        return ("", 404)
    return send_webp(img.image_data)
