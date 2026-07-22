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
from sqlalchemy.orm import aliased

from ..extensions import db
from ..models import TeaPost, TeaPostLike, User, UserFollow
from ..services.notification_service import notify

teahouse_bp = Blueprint("teahouse", __name__, url_prefix="/teahouse")

TEA_POST_MAX_LEN = 280


def _visible_query(query, viewer):
    """对 viewer 可见的帖子：未隐藏，或本人/超级管理员可见自己被隐藏的帖子。

    非超级管理员额外隐藏「已删除」（admin_del）作者的帖子；但保留「已注销」
    （user_del）与「纪念」（mourning）作者的帖子（其作者名显示对应占位昵称）。
    """
    if viewer.is_authenticated and viewer.is_super_admin:
        return query
    query = query.join(User, TeaPost.user_id == User.id).filter(
        User.status != "admin_del"
    )
    if viewer.is_authenticated:
        return query.filter(
            or_(TeaPost.is_hidden.is_(False), TeaPost.user_id == viewer.id)
        )
    return query.filter(TeaPost.is_hidden.is_(False))


def _build_stats(posts):
    """预计算一组帖子的点赞数、本人是否点赞、直接回复数，返回 {post_id: {...}}。"""
    ids = [p.id for p in posts]
    stats = {pid: {"like_count": 0, "liked": False, "reply_count": 0} for pid in ids}
    if not ids:
        return stats
    if current_user.is_authenticated:
        liked = TeaPostLike.query.filter(
            TeaPostLike.user_id == current_user.id,
            TeaPostLike.post_id.in_(ids),
        ).all()
        for l in liked:
            stats[l.post_id]["liked"] = True
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
    return stats


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
        # 最热：按（点赞数 + 直接回复数）降序，再按时间兜底
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
        hot_score = func.coalesce(like_sub, 0) + func.coalesce(reply_sub, 0)
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


# ---------------- 发帖 ----------------
@teahouse_bp.route("/post", methods=["POST"])
@login_required
def create_post():
    if current_user.is_muted:
        flash("你已被禁言，暂时无法发帖", "warning")
        return redirect(url_for("teahouse.index"))
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("帖子内容不能为空", "warning")
        return redirect(url_for("teahouse.index"))
    if len(content) > TEA_POST_MAX_LEN:
        flash(f"帖子内容不能超过 {TEA_POST_MAX_LEN} 字", "warning")
        return redirect(url_for("teahouse.index"))
    db.session.add(TeaPost(user_id=current_user.id, content=content))
    db.session.commit()
    flash("发布成功", "success")
    return redirect(url_for("teahouse.index"))


# ---------------- 帖子详情 / 回复 ----------------
@teahouse_bp.route("/<int:post_id>")
def post_detail(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    if p.is_hidden and not (
        current_user.is_authenticated
        and (current_user.id == p.user_id or current_user.is_super_admin)
    ):
        abort(404)
    # 向上追溯祖先链（root …… 直接父帖），用于详情页的“原帖链”
    chain = []
    node = p.parent
    while node is not None:
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
        hot_score = func.coalesce(like_sub, 0) + func.coalesce(reply_sub, 0)
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


@teahouse_bp.route("/<int:post_id>/reply", methods=["POST"])
@login_required
def reply(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if current_user.is_muted:
        if is_xhr:
            return jsonify({"ok": False, "error": "你已被禁言，暂时无法回复"})
        flash("你已被禁言，暂时无法回复", "warning")
        return redirect(url_for("teahouse.post_detail", post_id=post_id))
    content = (request.form.get("content") or "").strip()
    if not content:
        if is_xhr:
            return jsonify({"ok": False, "error": "回复内容不能为空"})
        flash("回复内容不能为空", "warning")
        return redirect(url_for("teahouse.post_detail", post_id=post_id))
    if len(content) > TEA_POST_MAX_LEN:
        if is_xhr:
            return jsonify({"ok": False, "error": f"回复内容不能超过 {TEA_POST_MAX_LEN} 字"})
        flash(f"回复内容不能超过 {TEA_POST_MAX_LEN} 字", "warning")
        return redirect(url_for("teahouse.post_detail", post_id=post_id))

    reply_post = TeaPost(user_id=current_user.id, parent_id=post_id, content=content)
    db.session.add(reply_post)
    db.session.commit()
    # 通知被回复帖子的作者（非本人）
    if p.user_id != current_user.id:
        notify(
            p.user_id,
            f"{current_user.nickname} 回复了你在茶馆的帖子：{content[:30]}",
            type_="teahouse",
        )

    if is_xhr:
        # 局部提交：渲染新回复 HTML 并刷新回复数，不整页刷新
        stats = {reply_post.id: {"like_count": 0, "liked": False, "reply_count": 0}}
        reply_macro = get_template_attribute("teahouse/_post_item.html", "render_reply")
        reply_html = reply_macro(reply_post, stats, root_id=post_id)
        return jsonify({
            "ok": True,
            "action": "reply",
            "reply_html": reply_html,
            "reply_count": TeaPost.query.filter_by(parent_id=post_id).count(),
        })
    flash("回复成功", "success")
    return redirect(url_for("teahouse.post_detail", post_id=post_id))


# ---------------- 点赞 ----------------
@teahouse_bp.route("/<int:post_id>/like", methods=["POST"])
@login_required
def like(post_id):
    p = db.session.get(TeaPost, post_id)
    if not p:
        abort(404)
    existing = TeaPostLike.query.filter_by(
        user_id=current_user.id, post_id=post_id
    ).first()
    if existing:
        db.session.delete(existing)
        now_liked = False
    else:
        db.session.add(TeaPostLike(user_id=current_user.id, post_id=post_id))
        # 点赞不再额外通知，避免刷屏
        now_liked = True
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        # 局部提交：返回新状态供前端切换按钮，不整页刷新
        return jsonify({
            "ok": True,
            "action": "like",
            "state": now_liked,
            "count": TeaPostLike.query.filter_by(post_id=post_id).count(),
        })
    return redirect(request.referrer or url_for("teahouse.post_detail", post_id=post_id))


# ---------------- 外链中转（离开本站确认页） ----------------
@teahouse_bp.route("/leave")
def leave():
    url = (request.args.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return redirect(url_for("main.index"))
    return render_template("teahouse/leave.html", url=url)
