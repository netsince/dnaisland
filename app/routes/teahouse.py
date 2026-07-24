import re
from datetime import datetime

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
from sqlalchemy import func, or_
from sqlalchemy.orm import aliased, joinedload

from ..decorators import block_if_muted
from ..extensions import db
from ..models import (
    Card,
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
from ..services.image_service import compress_image, raw_bytes_to_webp_data_url
from ..services.notification_service import notify
from ..services.sticker_service import sanitize_stickers
from ..utils import get_user_by_username, rate_hit, respond, toggle_relation

# 配图：仅支持单图
TEA_MAX_IMAGES = 1
TEA_IMAGE_MAX_EDGE = 1280
TEA_IMAGE_QUALITY = 82


def _calc_teahouse_hot_score_expr():
    """计算茶馆帖子的重力时间衰减热度得分 (Hacker News Gravity Model).

    公式：Hot Score = (Likes + Replies*1.5 + 1) / (AgeInHours + 2)^1.5
    保证最新发布的内容能在热度列表有一席之地，同时老热帖随着时间推移热度自然衰减。
    """
    from sqlalchemy import literal_column
    child = aliased(TeaPost)
    like_sub = (
        db.session.query(func.count(TeaPostLike.post_id))
        .filter(TeaPostLike.post_id == TeaPost.id)
        .correlate(TeaPost)
        .scalar_subquery()
    )
    reply_sub = (
        db.session.query(func.count(child.id))
        .filter(child.parent_id == TeaPost.id)
        .correlate(TeaPost)
        .scalar_subquery()
    )
    interactions = (func.coalesce(like_sub, 0) * 1.0) + (func.coalesce(reply_sub, 0) * 1.5)

    if db.engine.name == "sqlite":
        age_hours = (func.julianday("now") - func.julianday(TeaPost.created_at)) * 24.0
    else:
        age_hours = func.timestampdiff(literal_column("HOUR"), TeaPost.created_at, func.now())

    return (interactions + 1.0) / func.pow(age_hours + 2.0, 1.5)


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


def _visible_query(query, viewer):
    """对 viewer 可见的帖子：未隐藏、未删除，或本人/超级管理员可见自己被隐藏的帖子。

    非超级管理员额外隐藏「已删除」（admin_del）作者的帖子；但保留「已注销」
    （user_del）与「纪念」（mourning）作者的帖子（其作者名显示对应占位昵称）。
    """
    if viewer.is_authenticated and viewer.is_super_admin:
        return query.options(
            joinedload(TeaPost.card).joinedload(Card.images)
        )
    query = query.join(User, TeaPost.user_id == User.id).filter(
        User.status != "admin_del"
    )
    if viewer.is_authenticated:
        return query.filter(
            or_(TeaPost.is_hidden.is_(False), TeaPost.user_id == viewer.id),
            TeaPost.is_deleted.is_(False),
        ).options(
            joinedload(TeaPost.card).joinedload(Card.images),
            joinedload(TeaPost.images),
        )
    return query.filter(
        TeaPost.is_hidden.is_(False), TeaPost.is_deleted.is_(False)
    ).options(joinedload(TeaPost.card).joinedload(Card.images))


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


def _build_stats(posts):
    """预计算一组帖子的点赞数、本人是否点赞、直接回复数，返回 {post_id: {...}}。"""
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
    for pid, cnt in (
        db.session.query(TeaPostLike.post_id, func.count())
        .filter(TeaPostLike.post_id.in_(ids))
        .group_by(TeaPostLike.post_id)
        .all()
    ):
        stats[pid]["like_count"] = cnt
    for pid, cnt in (
        db.session.query(TeaPost.parent_id, func.count())
        .filter(TeaPost.parent_id.in_(ids))
        .group_by(TeaPost.parent_id)
        .all()
    ):
        stats[pid]["reply_count"] = cnt
    rows = (
        db.session.query(TeaPostTopic.post_id, TeaTopic.id, TeaTopic.name)
        .join(TeaTopic, TeaTopic.id == TeaPostTopic.topic_id)
        .filter(TeaPostTopic.post_id.in_(ids))
        .all()
    )
    for pid, tid, tname in rows:
        stats[pid]["topics"].append({"id": tid, "name": tname})
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


# ---------------- 推荐流（首页） ----------------
@teahouse_bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    default_sort = "follow" if current_user.is_authenticated else "hot"
    sort = request.args.get("sort", default_sort)
    q = TeaPost.query.filter(TeaPost.parent_id.is_(None))
    q = _visible_query(q, current_user)

    # 只看关注 / 只看粉丝：需登录，否则跳登录
    if sort in ("follow", "fans"):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if sort == "follow":
            sub = db.session.query(UserFollow.following_id).filter(
                UserFollow.follower_id == current_user.id
            )
        else:
            sub = db.session.query(UserFollow.follower_id).filter(
                UserFollow.following_id == current_user.id
            )
        q = q.filter(TeaPost.user_id.in_(sub))

    if sort == "random":
        # 随机看：MySQL 用 RAND()；翻页会重新随机
        q = q.order_by(db.func.rand())
    elif sort == "hot":
        # 最热：按 Hacker News 重力时间衰减公式计算热度分降序，时间兜底
        hot_score = _calc_teahouse_hot_score_expr()
        q = q.order_by(hot_score.desc(), TeaPost.created_at.desc())
    else:  # new
        q = q.order_by(TeaPost.created_at.desc())
    pagination = q.paginate(page=page, per_page=20, error_out=False)
    posts = pagination.items
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
    sub = db.session.query(TeaPostFavorite.post_id).filter(
        TeaPostFavorite.user_id == current_user.id
    )
    q = TeaPost.query.filter(TeaPost.id.in_(sub))
    q = _visible_query(q, current_user)
    q = q.join(TeaPostFavorite, TeaPostFavorite.post_id == TeaPost.id)
    q = q.order_by(TeaPostFavorite.created_at.desc())
    pagination = q.paginate(page=page, per_page=20, error_out=False)
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
    topic = db.session.get(TeaTopic, topic_id)
    if not topic:
        abort(404)
    page = request.args.get("page", 1, type=int)
    # 取带该话题的帖子的「根帖」集合（回复归并到其根帖）
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
    q = _visible_query(q, current_user)
    q = q.order_by(TeaPost.created_at.desc())
    pagination = q.paginate(page=page, per_page=20, error_out=False)
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
    content = (request.form.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        return respond(url_for("teahouse.index"), ok=False, status=400,
                       flash_msg="帖子内容不能为空", flash_cat="warning",
                       error="帖子内容不能为空")
    if len(content) > TEA_POST_MAX_LEN:
        return respond(url_for("teahouse.index"), ok=False, status=400,
                       flash_msg=f"帖子内容不能超过 {TEA_POST_MAX_LEN} 字", flash_cat="warning",
                       error=f"帖子内容不能超过 {TEA_POST_MAX_LEN} 字")
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
        redirect_url=url_for("teahouse.post_detail", post_id=post.id),
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
    if sort == "hot":
        hot_score = _calc_teahouse_hot_score_expr()
        rq = rq.order_by(hot_score.desc(), TeaPost.created_at.desc())
    else:
        rq = rq.order_by(TeaPost.created_at.desc())
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
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    cards = (
        Card.visible_to(current_user)
        .filter(Card.name.like(f"%{q}%"))
        .order_by(Card.view_count.desc())
        .limit(12)
        .all()
    )
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
    content = (request.form.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        return respond(url_for("teahouse.post_detail", post_id=post_id), ok=False, status=400,
                       flash_msg="回复内容不能为空", flash_cat="warning",
                       error="回复内容不能为空")
    if len(content) > TEA_POST_MAX_LEN:
        return respond(url_for("teahouse.post_detail", post_id=post_id), ok=False, status=400,
                       flash_msg=f"回复内容不能超过 {TEA_POST_MAX_LEN} 字", flash_cat="warning",
                       error=f"回复内容不能超过 {TEA_POST_MAX_LEN} 字")
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
    # 通知被回复帖子的作者（非本人）
    if p.user_id != current_user.id:
        notify(
            p.user_id,
            f"{current_user.nickname} 回复了你在茶馆的帖子：{content[:30]}",
            type_="teahouse",
        )
    # 解析正文 @提及，通知被提及用户
    _notify_mentions(content, reply_post, current_user)

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
    now_liked, count = toggle_relation(
        TeaPostLike.query.filter_by(
            user_id=current_user.id, post_id=post_id
        ).first(),
        TeaPostLike(user_id=current_user.id, post_id=post_id),
        TeaPostLike.query.filter_by(post_id=post_id),
    )
    if now_liked:
        # 点赞通知：被点赞者开启偏好、且非本人（可聚合：同帖不重复）
        if p.user_id != current_user.id:
            author = p.author
            if author and author.notify_like:
                dup = (
                    Notification.query.filter_by(
                        user_id=p.user_id, type="like", is_read=False
                    )
                    .filter(Notification.message.contains(f"/teahouse/{p.id}"))
                    .first()
                )
                if not dup:
                    url = url_for(
                        "teahouse.post_detail", post_id=p.id, _external=True
                    )
                    notify(
                        p.user_id,
                        f"{current_user.nickname} 赞了你在茶馆的帖子：{url}",
                        type_="like",
                    )
    else:
        # 聚合：取消点赞时移除该用户对此帖的未读点赞通知，避免重复刷屏
        Notification.query.filter_by(
            user_id=p.user_id, type="like", is_read=False
        ).filter(Notification.message.contains(f"/teahouse/{p.id}")).delete(
            synchronize_session=False
        )
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
    now_fav, count = toggle_relation(
        TeaPostFavorite.query.filter_by(
            user_id=current_user.id, post_id=post_id
        ).first(),
        TeaPostFavorite(user_id=current_user.id, post_id=post_id),
        TeaPostFavorite.query.filter_by(post_id=post_id),
    )
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
    if not p.can_edit(current_user):
        abort(403)
    content = (request.form.get("content") or "").strip()
    content, _ = sanitize_stickers(content, max_count=20)
    if not content:
        flash("帖子内容不能为空", "warning")
    elif len(content) > TEA_POST_MAX_LEN:
        flash(f"帖子内容不能超过 {TEA_POST_MAX_LEN} 字", "warning")
    else:
        p.content = content
        p.edited_at = datetime.utcnow()
        # 编辑窗口内允许改/清除关联角色卡（仅当表单显式提交 card_id 时，避免编辑内容时误清）
        if "card_id" in request.form:
            card_removed = request.form.get("card_removed") == "1"
            if card_removed:
                p.card_id = None
            else:
                card = _resolve_card(request.form.get("card_id"), current_user)
                p.card_id = card.id if card else None
        # 配图：仅当表单显式上传 images 文件或提交 image_removed 时才调整，未改动则保持原样
        if request.files.get("images") or "image_removed" in request.form:
            f = request.files.get("images")
            if f and f.filename:
                _attach_images(p, [f])
            elif request.form.get("image_removed") == "1":
                for old in list(p.images):
                    db.session.delete(old)
                p.images.clear()
        # 话题：仅当表单显式提交 topic 字段时才调整
        if "topic" in request.form:
            _set_single_topic(p, request.form.get("topic"))
        db.session.commit()
        _notify_mentions(content, p, current_user)
        flash("已更新", "success")
    return redirect(request.referrer or url_for("teahouse.post_detail", post_id=post_id))


@teahouse_bp.route("/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    p = _require_visible_post(post_id)
    if not (current_user.id == p.user_id or current_user.is_super_admin):
        abort(403)
    if p.is_deleted:
        abort(404)
    # 软删前递减该帖话题的计数（话题聚合页由可见性过滤，这里只修正粗略计数）
    for tp in TeaPostTopic.query.filter_by(post_id=p.id).all():
        _dec_topic_count(tp.topic_id)
    p.is_deleted = True
    p.deleted_at = datetime.utcnow()
    db.session.commit()
    flash("已删除", "success")
    if p.parent_id is None:
        return redirect(url_for("teahouse.index"))
    return redirect(url_for("teahouse.post_detail", post_id=p.parent_id))


# ---------------- 外链中转（离开本站确认页） ----------------
@teahouse_bp.route("/leave")
def leave():
    url = (request.args.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return redirect(url_for("main.index"))
    return render_template("teahouse/leave.html", url=url)
