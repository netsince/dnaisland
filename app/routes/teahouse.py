import random
import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

from flask import (
    Blueprint,
    abort,
    flash,
    get_template_attribute,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import and_, func, literal_column, or_
from sqlalchemy.orm import aliased, contains_eager, joinedload

from ..decorators import block_if_muted
from ..extensions import db
from ..models import (
    Card,
    CardImage,
    Notification,
    TeaPost,
    TeaPostFavorite,
    TeaPostImage,
    TeaPostLike,
    TeaPostTopic,
    TeaTopic,
    User,
    UserFollow,
)
from ..paging import IdListPagination
from ..services.image_service import raw_bytes_to_webp_data_url
from ..services.notification_service import notify
from ..services.sticker_service import sanitize_stickers
from ..utils import get_user_by_username, rate_hit, respond, toggle_relation

# 配图：仅支持单图
TEA_MAX_IMAGES = 1
TEA_IMAGE_MAX_EDGE = 1280
TEA_IMAGE_QUALITY = 82


def _order_by_teahouse_hot(q):
    """按热度排序：一次聚合赞数与回复数（避免排序时逐行关联子查询），再套用时间衰减。

    公式：Hot Score = (Likes + Replies*1.5 + 1) / (AgeInHours + 2)^1.5
    """
    likes_agg = (
        db.session.query(TeaPostLike.post_id, func.count(TeaPostLike.post_id).label("lc"))
        .group_by(TeaPostLike.post_id)
        .subquery("tp_likes_agg")
    )
    child = aliased(TeaPost)
    replies_agg = (
        db.session.query(child.parent_id, func.count(child.id).label("rc"))
        .group_by(child.parent_id)
        .subquery("tp_replies_agg")
    )
    q = q.outerjoin(likes_agg, likes_agg.c.post_id == TeaPost.id)
    q = q.outerjoin(replies_agg, replies_agg.c.parent_id == TeaPost.id)
    interactions = func.coalesce(likes_agg.c.lc, 0) * 1.0 + func.coalesce(replies_agg.c.rc, 0) * 1.5
    if db.engine.name == "sqlite":
        age_hours = (func.julianday("now") - func.julianday(TeaPost.created_at)) * 24.0
    else:
        age_hours = func.timestampdiff(literal_column("HOUR"), TeaPost.created_at, func.now())
    score = (interactions + 1.0) / func.pow(age_hours + 2.0, 1.5)
    return q.order_by(score.desc(), TeaPost.created_at.desc())


def _resolve_card(card_id_raw, viewer):
    """校验 card_id 是否属于「已通过且对 viewer 可见」的角色卡；非法时返回 None。

    关联范围：所有已通过可见卡（含 viewer 本人已通过但因被处罚而他人不可见的卡）。
    """
    if not card_id_raw:
        return None
    card_id = str(card_id_raw).strip()
    if not card_id:
        return None
    return Card.visible_to(viewer).filter(Card.id == card_id).first()


def _attach_images(post, image_files):
    """保存配图（仅单图）。image_files 为 FileStorage 列表，取第一张有效图压缩为 WebP data URL 存储。
    会先清空原图，便于编辑时整体替换。彻底去掉 base64 内联：前端以 multipart 文件上传，服务端转存。"""
    for old in list(post.images):
        db.session.delete(old)
    post.images.clear()
    for f in image_files:
        if not f or not getattr(f, "filename", ""):
            continue
        try:
            raw = f.read()
            data_url = raw_bytes_to_webp_data_url(
                raw, max_edge=TEA_IMAGE_MAX_EDGE, quality=TEA_IMAGE_QUALITY
            )
        except Exception:
            continue
        post.images.append(TeaPostImage(image_data=data_url))
        break  # 单图：仅取第一张
    return post


teahouse_bp = Blueprint("teahouse", __name__, url_prefix="/teahouse")

TEA_POST_MAX_LEN = 280
# 发帖 / 回复频率限制：窗口内（分钟）最多 N 条
TEA_RATE_WINDOW_MIN = 1
TEA_RATE_LIMIT = 5
# @提及匹配：@用户名（字母数字下划线）
TEA_MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")


def _apply_feed_image_loads(query):
    """为 feed 列表查询应用 eager-load，避免模板渲染时的惰性查询（N+1）：

    - 每张帖子只加载「第一张」配图（模板 render_images 只内联首图）；
    - 每张卡片只加载「第一张」图（模板只用到 card.images[0].slot）；
    - 预加载 post.author（模板每帖渲染作者昵称/头像，否则逐帖惰性查作者 = N+1）。

    CardImage.data / TeaPostImage.image_data 是 deferred，base64 不会被拉进内存，
    只有被访问的首图 image_data 才惰性加载。返回带 outerjoin 与 options 的 query。
    """
    fpi = (
        db.session.query(
            TeaPostImage.post_id.label("fpi_pid"),
            func.min(TeaPostImage.sort_order).label("fpi_so"),
        )
        .group_by(TeaPostImage.post_id)
        .subquery("first_post_img")
    )
    fpi_alias = aliased(TeaPostImage)
    fci = (
        db.session.query(
            CardImage.card_id.label("fci_cid"),
            func.min(CardImage.id).label("fci_id"),
        )
        .group_by(CardImage.card_id)
        .subquery("first_card_img")
    )
    fci_alias = aliased(CardImage)

    q = query.outerjoin(TeaPost.card)
    q = q.outerjoin(fpi, fpi.c.fpi_pid == TeaPost.id)
    q = q.outerjoin(
        fpi_alias,
        and_(fpi_alias.post_id == fpi.c.fpi_pid, fpi_alias.sort_order == fpi.c.fpi_so),
    )
    q = q.outerjoin(fci, fci.c.fci_cid == Card.id)
    q = q.outerjoin(
        fci_alias,
        and_(fci_alias.card_id == fci.c.fci_cid, fci_alias.id == fci.c.fci_id),
    )
    return q.options(
        contains_eager(TeaPost.images, alias=fpi_alias),
        contains_eager(TeaPost.card).contains_eager(Card.images, alias=fci_alias),
        joinedload(TeaPost.author),
    )


def _visible_query(query, viewer):
    """对 viewer 可见的帖子：未隐藏、未删除，或本人/超级管理员可见自己被隐藏的帖子。

    非超级管理员额外隐藏「已删除」（admin_del）作者的帖子；但保留「已注销」
    （user_del）与「纪念」（mourning）作者的帖子（其作者名显示对应占位昵称）。
    可见性过滤后复用 _apply_feed_image_loads 做 feed 所需的 eager-load。
    """
    if viewer.is_authenticated and viewer.is_super_admin:
        return _apply_feed_image_loads(query)
    if viewer.is_authenticated:
        query = query.filter(
            or_(TeaPost.is_hidden.is_(False), TeaPost.user_id == viewer.id),
            TeaPost.is_deleted.is_(False),
        )
    else:
        query = query.filter(TeaPost.is_hidden.is_(False), TeaPost.is_deleted.is_(False))
    query = query.join(User, TeaPost.user_id == User.id).filter(User.status != "admin_del")
    return _apply_feed_image_loads(query)


def _too_frequent(user_id):
    """窗口内发帖/回复数是否超过限制（防刷屏）。复用统一进程内限流。"""
    return rate_hit(
        "teahouse_post",
        limit=TEA_RATE_LIMIT,
        per=TEA_RATE_WINDOW_MIN * 60,
        key=user_id,
    )


def _require_visible_post(post_id):
    """校验帖子存在且对当前用户可见（未删除/未隐藏）；否则 404。返回 TeaPost 实例。"""
    p = db.get_or_404(TeaPost, post_id)
    if p.is_deleted and not current_user.is_super_admin:
        abort(404)
    if p.is_hidden and not (
        current_user.is_authenticated
        and (current_user.id == p.user_id or current_user.is_super_admin)
    ):
        abort(404)
    return p


def _notify_mentions(content, post, actor):
    """解析正文中的 @用户名，向被提及且非作者的用户发通知（同一帖去重）。"""
    url = url_for("teahouse.post_detail", post_id=post.id, _external=True)
    seen = set()
    for m in TEA_MENTION_RE.finditer(content or ""):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        u = get_user_by_username(name)
        if not u or u.id == actor.id:
            continue
        exists = (
            Notification.query.filter_by(user_id=u.id, type="mention", is_read=False)
            .filter(Notification.message.contains(f"/teahouse/{post.id}"))
            .first()
        )
        if exists:
            continue
        notify(u.id, f"{actor.nickname} 在茶馆提到了你：{url}", type_="mention")


# feed 每页帖子的计数聚合（点赞数/回复数/话题）按「当页 id 集合」缓存 30s，
# 避免每个请求都跑多轮 group by。本人赞/藏是 per-user 状态，始终实时计算（见 _build_stats）。
_STATS_CACHE: "OrderedDict[frozenset[int], tuple[float, Any]]" = OrderedDict()  # frozenset(ids) -> (ts, {pid: {...全局计数...}})
_STATS_TTL = 30


def _compute_global_stats(ids):
    """计算一组帖子的全局计数：点赞数、直接回复数、话题列表（不含 per-user 状态）。"""
    result = {pid: {"like_count": 0, "reply_count": 0, "topics": []} for pid in ids}
    for pid, cnt in (
        db.session.query(TeaPostLike.post_id, func.count())
        .filter(TeaPostLike.post_id.in_(ids))
        .group_by(TeaPostLike.post_id)
        .all()
    ):
        result[pid]["like_count"] = cnt
    for pid, cnt in (
        db.session.query(TeaPost.parent_id, func.count())
        .filter(TeaPost.parent_id.in_(ids))
        .group_by(TeaPost.parent_id)
        .all()
    ):
        result[pid]["reply_count"] = cnt
    rows = (
        db.session.query(TeaPostTopic.post_id, TeaTopic.id, TeaTopic.name)
        .join(TeaTopic, TeaTopic.id == TeaPostTopic.topic_id)
        .filter(TeaPostTopic.post_id.in_(ids))
        .all()
    )
    for pid, tid, tname in rows:
        result[pid]["topics"].append({"id": tid, "name": tname})
    return result


def _build_stats(posts):
    """预计算一组帖子的点赞数、本人是否点赞、直接回复数，返回 {post_id: {...}}。

    点赞数/回复数/话题为全局数据，按「当页帖子 id 集合」缓存 30s（计数滞后至多 30s，
    对 feed 角标可接受）；本人是否点赞/收藏是 per-user 状态，始终实时计算。
    """
    ids = [p.id for p in posts]
    stats = {
        pid: {
            "like_count": 0,
            "liked": False,
            "reply_count": 0,
            "favorited": False,
            "topics": [],
        }
        for pid in ids
    }
    if not ids:
        return stats
    now = time.time()
    key = frozenset(ids)
    cached = _STATS_CACHE.get(key)
    if cached is None or now - cached[0] >= _STATS_TTL:
        cached = (now, _compute_global_stats(ids))
        _STATS_CACHE[key] = cached
        while len(_STATS_CACHE) > 200:
            _STATS_CACHE.popitem(last=False)
    for pid, g in cached[1].items():
        stats[pid].update(g)
    if current_user.is_authenticated:
        liked = TeaPostLike.query.filter(
            TeaPostLike.user_id == current_user.id,
            TeaPostLike.post_id.in_(ids),
        ).all()
        for like in liked:
            stats[like.post_id]["liked"] = True
        faved = TeaPostFavorite.query.filter(
            TeaPostFavorite.user_id == current_user.id,
            TeaPostFavorite.post_id.in_(ids),
        ).all()
        for f in faved:
            stats[f.post_id]["favorited"] = True
    return stats


def _dec_topic_count(topic_id):
    if topic_id is None:
        return
    t = db.session.get(TeaTopic, topic_id)
    if t:
        t.post_count = max((t.post_count or 1) - 1, 0)


def _set_single_topic(post, topic_raw):
    """解析自由输入的话题（仅 1 个），查找/创建 TeaTopic 并维护关联与计数。"""
    name = (topic_raw or "").strip().strip("#").strip()
    if len(name) > 50:
        name = name[:50]
    existing = TeaPostTopic.query.filter_by(post_id=post.id).first()
    old_topic_id = existing.topic_id if existing else None
    if not name:
        if existing:
            db.session.delete(existing)
            _dec_topic_count(old_topic_id)
        return
    topic = TeaTopic.query.filter_by(name=name).first()
    if not topic:
        topic = TeaTopic(name=name, post_count=0)
        db.session.add(topic)
        db.session.flush()
    if existing:
        if existing.topic_id == topic.id:
            return
        db.session.delete(existing)
        _dec_topic_count(old_topic_id)
    db.session.add(TeaPostTopic(post_id=post.id, topic_id=topic.id))
    topic.post_count = (topic.post_count or 0) + 1


# ---------------------------------------------------------------------------
# 共享写操作 / Feed 组装逻辑：Web 路由与 App API 共用
# ---------------------------------------------------------------------------
def prepare_teapost_content(content):
    """清洗并校验茶馆帖子内容。

    返回 (clean, error)：error 为非空字符串表示校验失败（空 / 超长），clean 为已
    净化的内容（贴纸已处理）。两端发帖/回复统一走此函数，保证校验口径一致。
    """
    clean = (content or "").strip()
    clean, _ = sanitize_stickers(clean, max_count=20)
    if not clean:
        return None, "内容不能为空"
    if len(clean) > TEA_POST_MAX_LEN:
        return None, f"内容不能超过 {TEA_POST_MAX_LEN} 字"
    return clean, None


def toggle_teapost_like(viewer, post):
    """切换 viewer 对 post 的点赞状态，并处理点赞通知（含去重与取消清理）。

    返回 (now_liked, count)。仅把变更加入会话，由调用方提交。
    """
    now_liked, count = toggle_relation(
        TeaPostLike.query.filter_by(user_id=viewer.id, post_id=post.id).first(),
        TeaPostLike(user_id=viewer.id, post_id=post.id),
        TeaPostLike.query.filter_by(post_id=post.id),
    )
    if now_liked:
        if post.user_id != viewer.id:
            author = post.author
            if author and author.notify_like:
                dup = (
                    Notification.query.filter_by(
                        user_id=post.user_id, type="like", is_read=False
                    )
                    .filter(Notification.message.contains(f"/teahouse/{post.id}"))
                    .first()
                )
                if not dup:
                    url = url_for("teahouse.post_detail", post_id=post.id, _external=True)
                    notify(post.user_id, f"{viewer.nickname} 赞了你在茶馆的帖子：{url}", type_="like")
    else:
        Notification.query.filter_by(user_id=post.user_id, type="like", is_read=False).filter(
            Notification.message.contains(f"/teahouse/{post.id}")
        ).delete(synchronize_session=False)
    return now_liked, count


def toggle_teapost_favorite(viewer, post):
    """切换 viewer 对 post 的收藏状态。返回 (now_fav, count)。"""
    return toggle_relation(
        TeaPostFavorite.query.filter_by(user_id=viewer.id, post_id=post.id).first(),
        TeaPostFavorite(user_id=viewer.id, post_id=post.id),
        TeaPostFavorite.query.filter_by(post_id=post.id),
    )


def notify_teapost_reply(post, parent, actor):
    """回复后通知：被回复作者（非本人）+ 正文 @提及用户。Web 与 App 共用。"""
    if parent.user_id != actor.id:
        notify(
            parent.user_id,
            f"{actor.nickname} 回复了你在茶馆的帖子：{post.content[:30]}",
            type_="teahouse",
        )
    _notify_mentions(post.content, post, actor)


def teahouse_feed_page(viewer, sort, page, per_page, topic_id=None):
    """返回 feed 根帖分页：(posts, total, pages, has_next)。

    支持 sort=hot|new 与可选 topic_id 过滤，供 Web 与 App 共用。
    follow/fans/random 等 Web 专属排序不在此函数内。
    """
    q = TeaPost.query.filter(TeaPost.parent_id.is_(None))
    q = _visible_query(q, viewer)
    if topic_id:
        sub = db.session.query(TeaPostTopic.post_id).filter_by(topic_id=topic_id)
        q = q.filter(TeaPost.id.in_(sub))
    if sort == "hot":
        sig = (
            "teahouse_hot_admin"
            if (viewer.is_authenticated and viewer.is_super_admin)
            else "teahouse_hot"
        )
        ids = _hot_post_order(
            sig,
            lambda: _visible_query(
                TeaPost.query.filter(TeaPost.parent_id.is_(None)), viewer
            ),
        )
        if page < 1:
            page = 1
        start = (page - 1) * per_page
        slice_ids = ids[start : start + per_page]
        posts = []
        if slice_ids:
            fetched = {
                p.id: p
                for p in _visible_query(
                    TeaPost.query.filter(TeaPost.id.in_(slice_ids)), viewer
                ).all()
            }
            posts = [fetched[pid] for pid in slice_ids if pid in fetched]
        total = len(ids)
        pages = max(1, (total + per_page - 1) // per_page)
        return posts, total, pages, page < pages
    # new
    q = q.order_by(TeaPost.created_at.desc())
    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return pag.items, pag.total, pag.pages, pag.has_next


# 茶馆「最热」排序结果缓存：每次请求都要对整表做点赞/回复数聚合 + 时间衰减排序 +
# COUNT 分页；60s 内顺序变化极小，故缓存有序帖子 id 列表，分页直接切片，
# 免去每请求重算。缓存基于「全局可见」集合（超管可见范围不同，故 key 区分）。
_HOT_POST_CACHE: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()  # signature -> (ts, [post_id,...])
_HOT_POST_TTL = 60


def _hot_post_order(signature, build_query):
    now = time.time()
    hit = _HOT_POST_CACHE.get(signature)
    if hit is not None and now - hit[0] < _HOT_POST_TTL:
        return hit[1]
    q = build_query()
    q = _order_by_teahouse_hot(q)
    ids = [pid for (pid,) in q.with_entities(TeaPost.id).all()]
    _HOT_POST_CACHE[signature] = (now, ids)
    while len(_HOT_POST_CACHE) > 50:
        _HOT_POST_CACHE.popitem(last=False)
    return ids


# ---------------- 推荐流（首页） ----------------
@teahouse_bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    default_sort = "hot"
    sort = request.args.get("sort", default_sort)
    per_page = 20

    # Web 专属排序：只看关注 / 只看粉丝 / 随机
    if sort in ("follow", "fans"):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        q = TeaPost.query.filter(TeaPost.parent_id.is_(None))
        q = _visible_query(q, current_user)
        if sort == "follow":
            sub = db.session.query(UserFollow.following_id).filter(
                UserFollow.follower_id == current_user.id
            )
        else:
            sub = db.session.query(UserFollow.follower_id).filter(
                UserFollow.following_id == current_user.id
            )
        q = q.filter(TeaPost.user_id.in_(sub))
        q = q.order_by(TeaPost.created_at.desc())
        pagination = q.paginate(page=page, per_page=per_page, error_out=False)
        posts = pagination.items
    elif sort == "random":
        # 用「随机起始页」替代 db.func.rand()：RAND() 会触发全表扫描 + filesort，
        # 数据量大时极慢。随机页后仍走正常 paginate，对“随便逛逛”场景足够。
        q = TeaPost.query.filter(TeaPost.parent_id.is_(None))
        q = _visible_query(q, current_user)
        total = q.count()
        if total > per_page:
            page = random.randint(1, (total + per_page - 1) // per_page)
        pagination = q.order_by(TeaPost.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        posts = pagination.items
    else:  # hot / new：与 App 共用同一组装逻辑
        posts, total, pages, has_next = teahouse_feed_page(current_user, sort, page, per_page)
        pagination = IdListPagination(posts, page, per_page, total)
    stats = _build_stats(posts)
    return render_template(
        "teahouse/feed.html",
        posts=posts,
        stats=stats,
        pagination=pagination,
        args={"sort": sort},
        sort=sort,
        max_len=TEA_POST_MAX_LEN,
    )


# ---------------- 我的收藏列表 ----------------
@teahouse_bp.route("/favorites")
@login_required
def favorites():
    page = request.args.get("page", 1, type=int)
    pagination = favorites_page(current_user, page)
    posts = pagination.items
    stats = _build_stats(posts)
    return render_template(
        "teahouse/favorites.html",
        posts=posts,
        stats=stats,
        pagination=pagination,
        max_len=TEA_POST_MAX_LEN,
    )


@teahouse_bp.route("/topic/<int:topic_id>")
def topic_detail(topic_id):
    page = request.args.get("page", 1, type=int)
    topic, pagination = topic_posts_page(topic_id, current_user, page)
    if topic is None:
        abort(404)
    posts = pagination.items
    stats = _build_stats(posts)
    return render_template(
        "teahouse/topic.html",
        topic=topic,
        posts=posts,
        stats=stats,
        pagination=pagination,
        max_len=TEA_POST_MAX_LEN,
    )


# ---------------- 发帖 ----------------
@teahouse_bp.route("/post", methods=["POST"])
@login_required
@block_if_muted(message="你已被禁言，暂时无法发帖")
def create_post():
    content, err_msg = prepare_teapost_content(request.form.get("content"))
    if err_msg:
        return respond(url_for("teahouse.index"), ok=False, status=400,
                       flash_msg=err_msg, flash_cat="warning", error=err_msg)
    if _too_frequent(current_user.id):
        return respond(url_for("teahouse.index"), ok=False, status=429,
                       flash_msg="发帖太频繁了，请稍后再试", flash_cat="warning",
                       error="发帖太频繁了，请稍后再试")
    post = TeaPost(user_id=current_user.id, content=content)
    card = _resolve_card(request.form.get("card_id"), current_user)
    if card:
        post.card_id = card.id
    db.session.add(post)
    db.session.flush()  # 先拿到 post.id，再挂配图
    images_files = request.files.getlist("images")
    if images_files:
        _attach_images(post, images_files)
    _set_single_topic(post, request.form.get("topic"))
    db.session.commit()
    _notify_mentions(content, post, current_user)

    return respond(
        url_for("teahouse.post_detail", post_id=post.id),
        flash_msg="发布成功",
        flash_cat="success",
        action="post",
    )


# ---------------- 帖子详情 / 回复 ----------------
@teahouse_bp.route("/<int:post_id>")
def post_detail(post_id):
    p = _require_visible_post(post_id)
    # 向上追溯祖先链（root …… 直接父帖），用于详情页的“原帖链”
    chain = []
    node = p.parent
    while node is not None:
        if not node.is_deleted:
            chain.append(node)
        node = node.parent
    chain.reverse()  # 顺序：[最顶原帖, ..., 直接父帖]
    root = chain[0] if chain else p
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "new")

    # 回复列表：当前帖的直接子回复，SQL 层排序 + 分页（只铺一层，跟帖的跟帖点进去看）
    rq = TeaPost.query.filter(TeaPost.parent_id == p.id)
    rq = _visible_query(rq, current_user)
    rq = _order_by_teahouse_hot(rq) if sort == "hot" else rq.order_by(TeaPost.created_at.desc())
    reply_pagination = rq.paginate(page=page, per_page=20, error_out=False)
    replies = reply_pagination.items

    # 统计覆盖：当前帖 + 本页回复 + 祖先链（保证各卡“X 回复/赞”准确）
    stats = _build_stats(list(dict.fromkeys([p] + replies + chain)))

    focus_id = p.id if p != root else None
    return render_template(
        "teahouse/post.html",
        post=p,
        chain=chain,
        root=root,
        replies=replies,
        reply_pagination=reply_pagination,
        reply_args={"sort": sort, "post_id": post_id},
        stats=stats,
        sort=sort,
        focus_id=focus_id,
        max_len=TEA_POST_MAX_LEN,
    )


@teahouse_bp.route("/card-search")
@login_required
def card_search():
    """发帖时搜索可关联的角色卡（所有已通过且对当前用户可见的卡）。"""
    from ..routes.card_lists import search_cards_for_linking

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    cards = search_cards_for_linking(current_user, q)
    items = []
    for c in cards:
        cover = c.images[0] if c.images else None
        image_url = (
            url_for("user.card_image", card_id=c.id, slot=cover.slot)
            if cover
            else None
        )
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "intro": (c.intro or "")[:40],
                "image": image_url,
            }
        )
    return jsonify(items)


@teahouse_bp.route("/<int:post_id>/reply", methods=["POST"])
@login_required
@block_if_muted(message="你已被禁言，暂时无法回复")
def reply(post_id):
    p = _require_visible_post(post_id)
    content, err_msg = prepare_teapost_content(request.form.get("content"))
    if err_msg:
        return respond(url_for("teahouse.post_detail", post_id=post_id), ok=False, status=400,
                       flash_msg=err_msg, flash_cat="warning", error=err_msg)
    if _too_frequent(current_user.id):
        return respond(url_for("teahouse.post_detail", post_id=post_id), ok=False, status=429,
                       flash_msg="回复太频繁了，请稍后再试", flash_cat="warning",
                       error="回复太频繁了，请稍后再试")

    reply_post = TeaPost(user_id=current_user.id, parent_id=post_id, content=content)
    card = _resolve_card(request.form.get("card_id"), current_user)
    if card:
        reply_post.card_id = card.id
    db.session.add(reply_post)
    db.session.flush()  # 先拿到 reply_post.id，再挂配图
    images_files = request.files.getlist("images")
    if images_files:
        _attach_images(reply_post, images_files)
    _set_single_topic(reply_post, request.form.get("topic"))
    db.session.commit()
    # 通知被回复作者 + 正文 @提及用户
    notify_teapost_reply(reply_post, p, current_user)

    # 局部提交：渲染新回复 HTML 并刷新回复数，不整页刷新
    stats = {reply_post.id: {"like_count": 0, "liked": False, "reply_count": 0}}
    reply_macro = get_template_attribute("teahouse/_post_item.html", "render_reply")
    reply_html = reply_macro(reply_post, stats, root_id=post_id)
    return respond(
        url_for("teahouse.post_detail", post_id=post_id),
        flash_msg="回复成功", flash_cat="success",
        action="reply",
        reply_html=reply_html,
        reply_count=TeaPost.query.filter_by(parent_id=post_id).count(),
    )


# ---------------- 点赞 ----------------
@teahouse_bp.route("/<int:post_id>/like", methods=["POST"])
@login_required
def like(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    if p.is_deleted and not current_user.is_super_admin:
        abort(404)
    now_liked, count = toggle_teapost_like(current_user, p)
    db.session.commit()
    return respond(
        request.referrer or url_for("teahouse.post_detail", post_id=post_id),
        action="like",
        state=now_liked,
        count=count,
    )


@teahouse_bp.route("/<int:post_id>/favorite", methods=["POST"])
@login_required
def favorite(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    if p.is_deleted and not current_user.is_super_admin:
        abort(404)
    now_fav, count = toggle_teapost_favorite(current_user, p)
    return respond(
        request.referrer or url_for("teahouse.post_detail", post_id=post_id),
        action="favorite",
        state=now_fav,
        count=count,
    )


# ---------------- 编辑 / 删除（软删） ----------------
@teahouse_bp.route("/<int:post_id>/edit", methods=["POST"])
@login_required
def edit_post(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    if p.is_deleted and not current_user.is_super_admin:
        abort(404)
    content = (request.form.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    # 编辑窗口内允许改/清除关联角色卡（仅当表单显式提交 card_id 时，避免编辑内容时误清）
    card_action = None
    if "card_id" in request.form:
        if request.form.get("card_removed") == "1":
            card_action = ("remove",)
        else:
            card_action = ("set", request.form.get("card_id"))
    # 配图：仅当表单显式上传 images 文件或提交 image_removed 时才调整，未改动则保持原样
    remove_images = False
    if request.files.get("images"):
        _attach_images(p, [request.files.get("images")])
    elif request.form.get("image_removed") == "1":
        remove_images = True
    topic_raw = request.form.get("topic") if "topic" in request.form else None
    p, error = edit_teapost(current_user, p, content, card_action, topic_raw, remove_images)
    if error:
        return respond(
            request.referrer or url_for("teahouse.post_detail", post_id=post_id),
            ok=False,
            flash_msg=error,
            flash_cat="warning",
        )
    _notify_mentions(content, p, current_user)
    return respond(
        request.referrer or url_for("teahouse.post_detail", post_id=post_id),
        flash_msg="已更新",
        flash_cat="success",
        action="post",
    )


@teahouse_bp.route("/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    p = _require_visible_post(post_id)
    p, error = soft_delete_teapost(current_user, p)
    if error:
        abort(403 if "无权限" in error else 404)
    flash("已删除", "success")
    if p.parent_id is None:
        return redirect(url_for("teahouse.index"))
    return redirect(url_for("teahouse.post_detail", post_id=p.parent_id))


# ---------------------------------------------------------------------------
# 以下为 Web 与 App 共用的查询 / 写操作逻辑，避免两端分叉
# ---------------------------------------------------------------------------
def favorites_page(viewer, page, per_page=20):
    """当前 viewer 收藏的茶馆帖子（分页，按收藏时间倒序）。"""
    sub = db.session.query(TeaPostFavorite.post_id).filter(
        TeaPostFavorite.user_id == viewer.id
    )
    q = TeaPost.query.filter(TeaPost.id.in_(sub))
    q = _visible_query(q, viewer)
    q = q.join(TeaPostFavorite, TeaPostFavorite.post_id == TeaPost.id)
    q = q.order_by(TeaPostFavorite.created_at.desc())
    return q.paginate(page=page, per_page=per_page, error_out=False)


def topic_posts_page(topic_id, viewer, page, per_page=20):
    """取某话题下的根帖分页（回复归并到根帖）。topic 不存在返回 (None, None)。"""
    topic = db.session.get(TeaTopic, topic_id)
    if not topic:
        return None, None
    topic_post_ids = (
        db.session.query(TeaPostTopic.post_id).filter_by(topic_id=topic_id).subquery()
    )
    roots = (
        db.session.query(func.coalesce(TeaPost.parent_id, TeaPost.id).label("root_id"))
        .join(topic_post_ids, TeaPost.id == topic_post_ids.c.post_id)
        .distinct()
        .subquery()
    )
    q = TeaPost.query.filter(TeaPost.id.in_(db.session.query(roots.c.root_id)))
    q = _visible_query(q, viewer)
    q = q.order_by(TeaPost.created_at.desc())
    return topic, q.paginate(page=page, per_page=per_page, error_out=False)


def edit_teapost(viewer, post, content, card_action=None, topic_raw=None, remove_images=False):
    """编辑茶馆帖子核心逻辑（Web 与 App 共用）。

    - content: 已清洗后的正文（调用方需先 prepare_teapost_content）。
    - card_action: None 保持原样；("set", card_id) 关联；("remove",) 清除。
    - topic_raw: 提供则调整话题（含空串清除），None 保持原样。
    - remove_images: True 则清空所有配图。
    返回 (post, error)：error 非空表示校验/权限失败（已 flash 文案可供复用）。
    """
    if not post.can_edit(viewer):
        return post, "无权限编辑该帖子"
    if not content:
        return post, "帖子内容不能为空"
    if len(content) > TEA_POST_MAX_LEN:
        return post, f"帖子内容不能超过 {TEA_POST_MAX_LEN} 字"
    post.content = content
    post.edited_at = datetime.utcnow()
    if card_action is not None:
        if card_action[0] == "remove":
            post.card_id = None
        elif card_action[0] == "set":
            card = _resolve_card(card_action[1], viewer)
            post.card_id = card.id if card else None
    if topic_raw is not None:
        _set_single_topic(post, topic_raw)
    if remove_images:
        for old in list(post.images):
            db.session.delete(old)
        post.images.clear()
    db.session.commit()
    return post, None


def soft_delete_teapost(viewer, post):
    """软删一条茶馆帖子（作者或超管）。返回 (post, error)。"""
    if not (viewer.id == post.user_id or viewer.is_super_admin):
        return post, "无权限删除该帖子"
    if post.is_deleted:
        return post, "帖子已删除"
    for tp in TeaPostTopic.query.filter_by(post_id=post.id).all():
        _dec_topic_count(tp.topic_id)
    post.is_deleted = True
    post.deleted_at = datetime.utcnow()
    db.session.commit()
    return post, None


# ---------------- 外链中转（离开本站确认页） ----------------
@teahouse_bp.route("/leave")
def leave():
    url = (request.args.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return redirect(url_for("main.index"))
    return render_template("teahouse/leave.html", url=url)
