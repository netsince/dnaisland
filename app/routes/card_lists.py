"""Web 与 App 共用的卡片列表功能函数。

每个功能「一个字面意义的一个函数」：查询 + 分页 + 批量装配（封面/作者）都在这里，
Web 路由与 App API 路由都只调用这些函数，再各自做输出（HTML 模板 vs JSON）。
viewer 参数表示「以谁的身份看」（Web 传 current_user，App 传 JWT 用户），
避免两端可见性口径不一致。
"""
from ..models import Card, CardFavorite, CardLike, CardTag
from ..services.card_service import enrich_cards


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
    if sort == "new":
        q = q.order_by(Card.created_at.desc())
    else:  # likes
        q = _order_by_likes(q)
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
