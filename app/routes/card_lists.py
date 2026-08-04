"""Web 与 App 共用的卡片列表功能函数。

每个功能「一个字面意义的一个函数」：查询 + 分页 + 批量装配（封面/作者）都在这里，
Web 路由与 App API 路由都只调用这些函数，再各自做输出（HTML 模板 vs JSON）。
viewer 参数表示「以谁的身份看」（Web 传 current_user，App 传 JWT 用户），
避免两端可见性口径不一致。
"""
from datetime import datetime

from flask import current_app, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload

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
    SiteRecommendation,
    User,
    db,
)
from ..services.card_service import build_export_package, enrich_cards
from ..utils import toggle_relation


def _paginated_cards(query, page, per_page):
    """分页 + 批量装配，返回 (pagination, cards)。"""
    pag = query.paginate(page=page, per_page=per_page, error_out=False)
    return pag, enrich_cards(pag.items)


def explore_cards(viewer, page=1, gender="", tag=None, sort="hot", per_page=24):
    """探索页：sort=hot|new|likes，支持 gender/tag 过滤。Web 与 App 共用。"""
    if sort not in ("hot", "new", "likes"):
        sort = "hot"
    from ..routes.main import _order_by_hot, _order_by_likes, _paginate_hot_cards

    if sort == "hot":
        # 热门排序结果按「可见性视角 + 过滤条件」缓存 60s，分页直接切片。
        def _base():
            bq = Card.visible_to(viewer)
            if gender:
                bq = bq.filter(Card.gender == gender)
            if tag:
                bq = (
                    bq.join(CardTag, CardTag.card_id == Card.id)
                    .filter(CardTag.tag == tag)
                    .distinct()
                )
            return bq

        vid = getattr(viewer, "id", "anon")
        signature = f"explore|v={vid}|g={gender or ''}|t={tag or ''}"
        pagination = _paginate_hot_cards(_base, signature, _order_by_hot, page, per_page)
        return pagination, enrich_cards(pagination.items)

    q = Card.visible_to(viewer)
    if gender:
        q = q.filter(Card.gender == gender)
    if tag:
        q = q.join(CardTag, CardTag.card_id == Card.id).filter(CardTag.tag == tag)
    q = (
        q.order_by(Card.created_at.desc())
        if sort == "new"
        else _order_by_likes(q)
    )
    if tag:
        q = q.distinct()
    return _paginated_cards(q, page, per_page)


def search_cards(viewer, q, sort="relevance", tag=None, page=1, per_page=12):
    """角色卡搜索（卡片部分）。用户/帖子搜索仍由各端独立处理。"""
    if not q.strip():
        return None, []
    from ..routes.main import _card_search_query

    query = _card_search_query(q, sort, tag, viewer)
    return _paginated_cards(query, page, per_page)


def profile_cards(viewer, username, page=1, per_page=12):
    """某用户的角色卡列表（含可见性/隐私过滤）。返回 (user, pagination, cards)。"""
    from ..utils import get_user_by_username

    u = get_user_by_username(username)
    if not u:
        raise ValueError("用户不存在")
    is_self = bool(viewer.is_authenticated and viewer.id == u.id)
    is_admin = bool(getattr(viewer, "is_super_admin", False))
    if is_self or is_admin:
        # 本人/管理员：显示该用户所有卡片（含待审/驳回），与原语义一致。
        q = Card.query.filter_by(author_id=u.id)
    else:
        q = Card.visible_to(viewer).filter(Card.author_id == u.id)
        q = q.filter(Card.visibility.in_(["public", "unlisted"]))
    q = q.order_by(Card.created_at.desc())
    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return u, pag, enrich_cards(pag.items)


def my_cards(viewer, page=1, per_page=12):
    """我的角色卡。"""
    q = Card.query.filter_by(author_id=viewer.id).order_by(Card.created_at.desc())
    return _paginated_cards(q, page, per_page)


def my_favorites(viewer, page=1, per_page=12):
    """我的收藏。"""
    q = (
        Card.visible_to(viewer)
        .join(CardFavorite, CardFavorite.card_id == Card.id)
        .filter(CardFavorite.user_id == viewer.id)
        .order_by(CardFavorite.created_at.desc())
    )
    return _paginated_cards(q, page, per_page)


def my_likes(viewer, page=1, per_page=12):
    """我的点赞。"""
    q = (
        Card.visible_to(viewer)
        .join(CardLike, CardLike.card_id == Card.id)
        .filter(CardLike.user_id == viewer.id)
        .order_by(CardLike.created_at.desc())
    )
    return _paginated_cards(q, page, per_page)


def recommend_items():
    """站长推荐：Web 与 App 共用一个函数。

    返回 items 列表，元素为：
      {"kind": "card", "card": Card, "note": str}（Card 已批量挂封面）
      {"kind": "user", "user": User, "note": str, "card_count": int}
    """
    recs = SiteRecommendation.query.order_by(
        SiteRecommendation.sort_order, SiteRecommendation.created_at
    ).all()
    user_ids = [
        int(r.ref_id) for r in recs if r.kind == "user" and str(r.ref_id).isdigit()
    ]
    card_counts = (
        dict(
            db.session.query(Card.author_id, db.func.count())
            .filter(Card.author_id.in_(user_ids))
            .group_by(Card.author_id)
            .all()
        )
        if user_ids
        else {}
    )

    items = []
    cover_cards = []
    for r in recs:
        if r.kind == "card":
            c = db.session.get(Card, r.ref_id)
            if c and c.status == "approved" and not c.is_hidden:
                items.append({"kind": "card", "card": c, "note": r.note})
                cover_cards.append(c)
        elif r.kind == "user":
            if str(r.ref_id).isdigit():
                u = db.session.get(User, int(r.ref_id))
                if u and u.status == "active" and not u.active_punishments:
                    items.append(
                        {
                            "kind": "user",
                            "user": u,
                            "note": r.note,
                            "card_count": card_counts.get(u.id, 0),
                        }
                    )
    enrich_cards(cover_cards)
    return items


def card_export_package(card_id, viewer):
    """复制/导出角色卡：Web 与 App 共用一个函数。

    返回 (card, package, error_code)；error_code 取值为：
      None      成功
      "unauth"  未登录
      "not_found"/"forbidden"  不存在或无权限
    核心逻辑（可见性判断、打包、复制统计去重累加）都在这里，两端只做响应包装。
    """
    if not getattr(viewer, "is_authenticated", False):
        return None, None, "unauth"
    card = db.session.get(Card, card_id)
    if not card:
        return None, None, "not_found"
    is_owner = viewer.id == card.author_id
    is_admin = bool(getattr(viewer, "is_super_admin", False))
    if not (card.is_public or is_owner or is_admin):
        return None, None, "forbidden"

    origin = request.host_url.rstrip("/")
    copier = viewer.username
    copier_ip = (
        (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown")
        .split(",")[0]
        .strip()
    )
    package = build_export_package(
        card,
        origin=origin,
        copier=copier,
        copier_ip=copier_ip,
        platform_domain=origin,
    )

    # 复制统计：失败不应影响导出（与原语义一致）。
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        copied_today = CardCopyStat.query.filter(
            CardCopyStat.user_id == viewer.id,
            CardCopyStat.card_id == card.id,
            CardCopyStat.copied_at >= today_start,
        ).first()
        db.session.add(
            CardCopyStat(
                card_id=card.id,
                card_name=card.name,
                user_id=viewer.id,
                username=viewer.username,
                copier_ip=copier_ip,
            )
        )
        if copied_today is None:
            card.copy_count = (card.copy_count or 0) + 1
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.warning("记录角色卡复制统计失败: %s", exc)

    return card, package, None


def card_detail_core(card_id, viewer):
    """角色卡详情核心数据：Web 与 App 共用。

    返回 (card, data, error_code)；data 含：
      tags / dialogue / images / like_count / favorite_count / comment_count / liked / favorited
    error_code 为 None | "not_found" | "forbidden"。
    非作者/管理员访问时浏览量 +1（与原语义一致）。
    """
    card = db.session.get(Card, card_id)
    if not card:
        return None, None, "not_found"
    is_owner = bool(viewer.is_authenticated and viewer.id == card.author_id)
    is_admin = bool(getattr(viewer, "is_super_admin", False))
    if not (card.is_public or is_owner or is_admin):
        return None, None, "forbidden"

    if not is_owner and not is_admin:
        card.view_count = (card.view_count or 0) + 1
        db.session.commit()

    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = [
        {"user": d.user_text, "assistant": d.assistant_text}
        for d in CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
    ]
    images = {}
    for img in CardImage.query.filter_by(card_id=card.id).all():
        images[img.slot] = f"/card-image/{card.id}/{img.slot}"
    like_count = CardLike.query.filter_by(card_id=card.id).count()
    favorite_count = CardFavorite.query.filter_by(card_id=card.id).count()
    comment_count = Comment.query.filter_by(card_id=card.id, is_hidden=False).count()

    liked = False
    favorited = False
    if viewer.is_authenticated:
        liked = (
            CardLike.query.filter_by(user_id=viewer.id, card_id=card.id).first()
            is not None
        )
        favorited = (
            CardFavorite.query.filter_by(user_id=viewer.id, card_id=card.id).first()
            is not None
        )

    return card, {
        "tags": tags,
        "dialogue": dialogue,
        "images": images,
        "like_count": like_count,
        "favorite_count": favorite_count,
        "comment_count": comment_count,
        "liked": liked,
        "favorited": favorited,
    }, None


def card_comments_list(card_id, viewer, page=1, per_page=20, sort="latest"):
    """评论列表核心：Web 与 App 共用（查询 + 批量点赞统计 + 预载作者）。

    返回 (card, data, error_code)；data 含：
      pagination / like_counts / user_liked_ids / sort
    """
    card = db.session.get(Card, card_id)
    if not card:
        return None, None, "not_found"

    like_count_sub = (
        db.session.query(func.count().label("lc"))
        .filter(CommentLike.comment_id == Comment.id)
        .scalar_subquery()
    )
    q = Comment.query.options(
        joinedload(Comment.author),
        joinedload(Comment.reply_to).joinedload(Comment.author),
    ).filter_by(card_id=card_id, is_hidden=False)
    if sort == "hottest":
        q = q.order_by(
            Comment.is_pinned.desc(),
            like_count_sub.desc(),
            Comment.created_at.desc(),
            Comment.id.desc(),
        )
    else:
        q = q.order_by(
            Comment.is_pinned.desc(), Comment.created_at.desc(), Comment.id.desc()
        )
    pag = q.paginate(page=page, per_page=per_page, error_out=False)

    comment_ids = [cm.id for cm in pag.items]
    like_counts: dict[int, int] = {}
    user_liked_ids: set = set()
    if comment_ids:
        rows = (
            db.session.query(CommentLike.comment_id, func.count().label("cnt"))
            .filter(CommentLike.comment_id.in_(comment_ids))
            .group_by(CommentLike.comment_id)
            .all()
        )
        like_counts = dict(rows)
        if viewer.is_authenticated:
            ul = (
                db.session.query(CommentLike.comment_id)
                .filter(
                    CommentLike.user_id == viewer.id,
                    CommentLike.comment_id.in_(comment_ids),
                )
                .all()
            )
            user_liked_ids = {r[0] for r in ul}

    return card, {
        "pagination": pag,
        "like_counts": like_counts,
        "user_liked_ids": user_liked_ids,
        "sort": sort,
    }, None


def create_comment(card_id, viewer, content, reply_to_id=None, image_data=None):
    """发表评论核心：Web 与 App 共用（校验 + 回复验证 + 通知）。

    返回 (comment, error_code)；error_code：
      None | "not_found" | "muted" | "empty" | "too_long"
    图片上传的格式转换由两端各自处理后再传入 image_data。
    """
    from ..services.notification_service import notify
    from ..services.sticker_service import sanitize_stickers

    card = db.session.get(Card, card_id)
    if not card:
        return None, "not_found"
    if getattr(viewer, "is_muted", False):
        return None, "muted"

    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        return None, "empty"
    if len(content) > 500:
        return None, "too_long"

    parent = None
    valid_reply_id = None
    if reply_to_id:
        parent = db.session.get(Comment, reply_to_id)
        if parent and parent.card_id == card_id:
            valid_reply_id = parent.id
        else:
            parent = None

    cm = Comment(
        card_id=card_id,
        user_id=viewer.id,
        content=content,
        reply_to_id=valid_reply_id,
        image_data=image_data,
    )
    db.session.add(cm)

    if valid_reply_id and parent:
        if parent.user_id != viewer.id:
            notify(
                parent.user_id,
                f'{viewer.display_name} 回复了你的评论："{content[:30]}"',
                type_="comment_reply",
                related_card_id=card.id,
            )
    elif card.author_id != viewer.id:
        notify(
            card.author_id,
            f"{viewer.display_name} 评论了你的角色卡《{card.name}》",
            type_="card_comment",
            related_card_id=card.id,
        )
    db.session.commit()
    return cm, None


# ---------------------------------------------------------------------------
# 角色卡写操作：点赞 / 收藏（Web 与 App 共用同一开关逻辑）
# ---------------------------------------------------------------------------
def toggle_card_like(viewer, card_id):
    """切换 viewer 对 card_id 的点赞状态。

    返回 (card, now_active, count)：card 为 None 表示角色卡不存在。
    """
    card = db.session.get(Card, card_id)
    if card is None:
        return None, None, None
    now_active, count = toggle_relation(
        CardLike.query.filter_by(user_id=viewer.id, card_id=card_id).first(),
        CardLike(user_id=viewer.id, card_id=card_id),
        CardLike.query.filter_by(card_id=card_id),
    )
    return card, now_active, count


def toggle_card_favorite(viewer, card_id):
    """切换 viewer 对 card_id 的收藏状态。

    返回 (card, now_active, count)：card 为 None 表示角色卡不存在。
    """
    card = db.session.get(Card, card_id)
    if card is None:
        return None, None, None
    now_active, count = toggle_relation(
        CardFavorite.query.filter_by(user_id=viewer.id, card_id=card_id).first(),
        CardFavorite(user_id=viewer.id, card_id=card_id),
        CardFavorite.query.filter_by(card_id=card_id),
    )
    return card, now_active, count


def search_cards_for_linking(viewer, q, limit=12):
    """发帖时搜索可关联的角色卡：所有已通过且对 viewer 可见、名称命中关键字的卡。

    Web 与 App 共用同一查询，保证「发帖选卡」结果一致。q 为空时返回 []。
    """
    if not q:
        return []
    return (
        Card.visible_to(viewer)
        .filter(Card.name.like(f"%{q}%"))
        .order_by(Card.view_count.desc())
        .limit(limit)
        .all()
    )
