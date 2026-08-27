import html
import json
import os
from datetime import timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..decorators import block_if_muted
from ..extensions import db
from ..models import (
    Card,
    CardDialogueStyle,
    CardImage,
    CardTag,
    Comment,
    Punishment,
    Sponsor,
    TeaPost,
    Ticket,
    TicketCategory,
    TicketMessage,
    User,
    UserFollow,
)
from ..models.punishment import (
    APPEAL_ACCEPTED,
    APPEAL_PENDING,
    APPEAL_REJECTED,
    PUNISHMENT_TYPES,
)
from ..models.ticket import (
    MSG_ROLE_USER,
    TICKET_CLOSED,
    TICKET_OPEN,
    TICKET_STATUSES,
)
from ..services.card_edit_service import (
    resubmit_card,
    toggle_card_hidden,
    update_card_from_payload,
)
from ..services.comment_service import (
    delete_comment,
    pin_comment,
    toggle_comment_like,
)
from ..services.image_service import raw_bytes_to_webp_data_url
from ..services.notification_service import notifications_page, notify_super_admins
from ..services.profile_service import update_profile
from ..services.punishment_service import my_punishments_list, submit_punishment_appeal
from ..services.report_service import (
    REPORT_REASONS,
    REPORT_TARGETS,
    describe_report_target,
    submit_report,
)
from ..utils import (
    get_user_by_username,
    is_xhr,
    respond,
    status_counts,
)

# 审核状态徽章 HTML（与 macros/cards.html::status_badge 保持一致，供 AJAX 局部更新）
STATUS_BADGE_HTML = {
    "approved": '<span class="badge bg-success">已通过</span>',
    "rejected": '<span class="badge bg-danger">已拒绝</span>',
    "pending": '<span class="badge bg-warning text-dark">审核中</span>',
}
from ..routes.card_lists import (
    card_comments_list,
    card_detail_core,
    card_export_package,
    create_comment,
    profile_cards,
    toggle_card_favorite,
    toggle_card_like,
)
from ..routes.card_lists import my_cards as shared_my_cards
from ..routes.card_lists import my_favorites as shared_my_favorites
from ..routes.card_lists import my_likes as shared_my_likes
from ..routes.follow_lists import toggle_user_follow
from ..services.card_service import (
    attach_covers,
    load_card_images,
)
from ..services.image_service import send_webp

user_bp = Blueprint("user", __name__)


def _default_avatar_svg(ch):
    """生成一个首字母占位头像（SVG），避免无头像时返回 404 导致破图。"""
    ch = html.escape(ch or "?", quote=True)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        '<rect width="256" height="256" fill="#2a2738"/>'
        '<text x="128" y="140" fill="#b9b6d6" font-size="120" '
        'font-family="system-ui, sans-serif" text-anchor="middle" '
        'dominant-baseline="middle">' + ch + "</text>"
        "</svg>"
    )


@user_bp.route("/user/avatar/<int:user_id>")
def avatar(user_id):
    """用户头像：base64 data URL -> WEBP 二进制，避免内联膨胀 HTML。无头像时返回首字母占位图。"""
    u = db.session.get(User, user_id)
    if u and u.avatar:
        return send_webp(u.avatar, max_edge=256, quality=82)
    ch = (u.display_name or u.username or "?")[0] if u else "?"
    return Response(
        _default_avatar_svg(ch),
        mimetype="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@user_bp.route("/card-image/<card_id>/<slot>")
def card_image(card_id, slot):
    """角色卡图片（square/landscape/portrait）：base64 -> WEBP。"""
    if slot not in ("square", "landscape", "portrait"):
        abort(404)
    img = CardImage.query.filter_by(card_id=card_id, slot=slot).first()
    if not img or not img.data:
        abort(404)
    return send_webp(img.data, max_edge=1024, quality=82)

@user_bp.route("/user/<username>")
def profile(username):
    u = get_user_by_username(username)
    if not u:
        abort(404)
    is_self = current_user.is_authenticated and current_user.id == u.id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    # 禁止主页被访问：他人仅可见受限提示与处罚列表；本人与管理员可见完整主页
    restricted = (not is_self) and (not is_admin) and u.is_profile_banned

    page = request.args.get("page", 1, type=int)
    tab = request.args.get("tab", "cards")

    # 角色卡列表：与 App 共用 profile_cards 一个函数（含可见性/隐私过滤 + 批量装配）。
    _u2, pagination, cards = profile_cards(
        current_user, username, page=page, per_page=12
    )

    # 茶馆：我发布的帖子（顶级）/ 回帖（有父级）
    from ..models import TeaPost

    tp_query = TeaPost.query.filter_by(user_id=u.id)
    if not (is_self or is_admin):
        tp_query = tp_query.filter(TeaPost.is_hidden.is_(False))
    if tab == "teahouse_posts":
        tp_query = tp_query.filter(TeaPost.parent_id.is_(None))
    elif tab == "teahouse_replies":
        tp_query = tp_query.filter(TeaPost.parent_id.isnot(None))
    tp_pagination = tp_query.order_by(TeaPost.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    # 角色卡评论：用户在各角色卡下的回复
    cm_pagination = (
        Comment.query.filter_by(user_id=u.id)
        .order_by(Comment.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    # 批量附加评论所属角色卡（含封面），避免模板 N 次查询
    comment_cards = {}
    cm_card_ids = [c.card_id for c in cm_pagination.items if c.card_id]
    if cm_card_ids:
        cm_cards = Card.query.filter(Card.id.in_(cm_card_ids)).all()
        attach_covers(cm_cards, slot="square")
        comment_cards = {c.id: c for c in cm_cards}

    follower_count = UserFollow.query.filter_by(following_id=u.id).count()
    following_count = UserFollow.query.filter_by(follower_id=u.id).count()
    is_following = (
        current_user.is_authenticated
        and UserFollow.query.filter_by(
            follower_id=current_user.id, following_id=u.id
        ).first()
        is not None
    )
    return render_template(
        "user/profile.html",
        u=u,
        deleted=u.is_deleted,
        mourning=u.is_mourning,
        cards=cards,
        pagination=pagination,
        args={"username": username},
        tab=tab,
        tp_items=tp_pagination.items,
        tp_pagination=tp_pagination,
        tp_args={"username": username, "tab": tab},
        comments=cm_pagination.items,
        cm_pagination=cm_pagination,
        comment_cards=comment_cards,
        cm_args={"username": username, "tab": "comments"},
        is_self=is_self,
        is_admin=is_admin,
        restricted=restricted,
        punishments=u.active_punishments,
        follower_count=follower_count,
        following_count=following_count,
        is_following=is_following,
    )


@user_bp.route("/user/<username>/followers")
def followers(username):
    u = get_user_by_username(username)
    if not u:
        abort(404)
    is_self = current_user.is_authenticated and current_user.id == u.id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    restricted = (not is_self) and (not is_admin) and u.is_profile_banned

    page = request.args.get("page", 1, type=int)
    pagination = _paginate_follows(u, "followers", page)
    items = _follow_items(pagination.items, include_banned=is_self or is_admin)
    return render_template(
        "user/follow_list.html",
        u=u,
        kind="followers",
        endpoint="user.followers",
        items=items,
        pagination=pagination,
        args={"username": username},
        is_self=is_self,
        is_admin=is_admin,
        restricted=restricted,
    )


@user_bp.route("/user/<username>/following")
def following(username):
    u = get_user_by_username(username)
    if not u:
        abort(404)
    is_self = current_user.is_authenticated and current_user.id == u.id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    restricted = (not is_self) and (not is_admin) and u.is_profile_banned

    page = request.args.get("page", 1, type=int)
    pagination = _paginate_follows(u, "following", page)
    items = _follow_items(pagination.items, include_banned=is_self or is_admin)
    return render_template(
        "user/follow_list.html",
        u=u,
        kind="following",
        endpoint="user.following",
        items=items,
        pagination=pagination,
        args={"username": username},
        is_self=is_self,
        is_admin=is_admin,
        restricted=restricted,
    )


@user_bp.route("/settings/profile", methods=["GET", "POST"])
@login_required
def profile_edit():
    u = current_user
    if u.is_edit_profile_banned:
        flash("你当前被禁止更改资料", "warning")
        return redirect(url_for("user.profile", username=u.username))
    if request.method == "POST":
        avatar_file = request.files.get("avatar_file")
        error = update_profile(
            u,
            nickname=request.form.get("nickname"),
            bio=request.form.get("bio"),
            location=request.form.get("location"),
            website=request.form.get("website"),
            birthday_raw=request.form.get("birthday"),
            notify_like="notify_like" in request.form,
            avatar_bytes=avatar_file.read() if avatar_file and avatar_file.filename else None,
            remove_avatar=bool(request.form.get("remove_avatar")),
        )
        if error:
            flash(error, "warning")
            return render_template("user/profile_edit.html", u=u)
        flash("个人资料已更新", "success")
        return redirect(url_for("user.profile", username=u.username))

    return render_template("user/profile_edit.html", u=u)


@user_bp.route("/my/punishments")
@login_required
def my_punishments():

    items = my_punishments_list(current_user.id)
    return render_template(
        "user/my_punishments.html",
        items=items,
        punishment_types=PUNISHMENT_TYPES,
        appeal_pending=APPEAL_PENDING,
        appeal_accepted=APPEAL_ACCEPTED,
        appeal_rejected=APPEAL_REJECTED,
    )


@user_bp.route("/my/punishments/<int:punishment_id>/appeal", methods=["POST"])
@login_required
def punish_appeal(punishment_id):

    p = db.get_or_404(Punishment, punishment_id)
    if p.user_id != current_user.id:
        abort(404)
    reason = (request.form.get("appeal_reason") or "").strip()
    _p, msg = submit_punishment_appeal(current_user, p, reason)
    if msg:
        flash(msg, "warning")
    else:
        flash("申诉已提交，等待管理员处理（仅可申诉一次）", "success")
    return redirect(url_for("user.my_punishments"))


@user_bp.route("/card/<card_id>")
def card_detail(card_id):
    """角色卡详情：核心数据与 App 共用 card_detail_core 一个函数。"""
    card, data, err_code = card_detail_core(card_id, current_user)
    if err_code in ("not_found", "forbidden"):
        abort(404)

    author = card.author
    is_owner = current_user.is_authenticated and current_user.id == card.author_id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    focus_comment = request.args.get("comment", type=int)

    # 评论区与关联茶馆帖（页面级内容，web 独有）
    comments = (
        Comment.query.filter_by(card_id=card.id)
        .order_by(Comment.created_at.asc())
        .all()
    )
    visible_comments = [
        c
        for c in comments
        if (not c.is_hidden)
        and not (c.author and c.author.is_deleted and not is_admin)
        and not (
            c.author
            and c.author.is_comments_hidden
            and not (current_user.is_authenticated and current_user.id == c.author.id)
            and not is_admin
        )
    ]
    following = (
        current_user.is_authenticated
        and author is not None
        and UserFollow.query.filter_by(
            follower_id=current_user.id, following_id=author.id
        ).first()
        is not None
    )
    linked_posts = (
        TeaPost.query.filter(
            TeaPost.card_id == card.id,
            TeaPost.is_deleted.is_(False),
            TeaPost.is_hidden.is_(False),
        )
        .order_by(TeaPost.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "user/card_detail.html",
        card=card,
        author=author,
        tags=data["tags"],
        dialogue=data["dialogue"],
        images=load_card_images(card.id),
        is_owner=is_owner,
        is_admin=is_admin,
        like_count=data["like_count"],
        favorite_count=data["favorite_count"],
        comment_count=len(visible_comments),
        comments=visible_comments,
        linked_posts=linked_posts,
        liked=data["liked"],
        favorited=data["favorited"],
        following=following,
        focus_comment=focus_comment,
        # 赞助者 user_id 集合（JS 评论渲染时给昵称加红色星标）
        sponsor_ids=[sid for (sid,) in db.session.query(Sponsor.user_id).all()],
    )


@user_bp.route("/card/<card_id>/export")
def card_export(card_id):
    """导出角色卡为 dna-client 可识别的 JSON 下载。

    核心逻辑与 App 共用 card_export_package 一个函数。
    """
    card, package, err_code = card_export_package(card_id, current_user)
    if err_code == "unauth":
        return jsonify(
            error="请先登录后再复制角色卡",
            login_url=url_for("auth.login"),
        ), 401
    if err_code in ("not_found", "forbidden"):
        abort(404)

    # 紧凑 JSON：复制到剪贴板时不带缩进，显著减小体积，避免浏览器写入剪贴板失败
    body = json.dumps(package, ensure_ascii=False, separators=(",", ":"))
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="dna-card-{card.id}.json"'
    )
    return resp


@user_bp.route("/my/cards")
@login_required
def my_cards():
    page = request.args.get("page", 1, type=int)
    pagination, cards = shared_my_cards(current_user, page=page, per_page=12)
    stats = status_counts(Card, Card.query.filter_by(author_id=current_user.id))
    return render_template(
        "user/my_cards.html",
        cards=cards,
        pagination=pagination,
        args={},
        stats=stats,
        pending=stats.get("pending", 0),
        approved=stats.get("approved", 0),
        rejected=stats.get("rejected", 0),
    )


@user_bp.route("/my/favorites")
@login_required
def my_favorites():
    page = request.args.get("page", 1, type=int)
    pagination, cards = shared_my_favorites(current_user, page=page, per_page=12)
    return render_template(
        "user/card_list.html",
        cards=cards,
        pagination=pagination,
        args={},
        title="我的收藏",
        show_status=current_user.is_super_admin,
    )


@user_bp.route("/my/likes")
@login_required
def my_likes():
    page = request.args.get("page", 1, type=int)
    pagination, cards = shared_my_likes(current_user, page=page, per_page=12)
    return render_template(
        "user/card_list.html",
        cards=cards,
        pagination=pagination,
        args={},
        title="我点赞的",
        show_status=current_user.is_super_admin,
    )


@user_bp.route("/card/<card_id>/like", methods=["POST"])
@login_required
def card_like(card_id):
    card, now_active, count = toggle_card_like(current_user, card_id)
    if card is None:
        abort(404)
    return respond(
        url_for("user.card_detail", card_id=card_id),
        flash_msg="已点赞" if now_active else "已取消点赞",
        flash_cat="success" if now_active else "info",
        action="like",
        state=now_active,
        count=count,
    )


@user_bp.route("/card/<card_id>/favorite", methods=["POST"])
@login_required
def card_favorite(card_id):
    card, now_active, count = toggle_card_favorite(current_user, card_id)
    if card is None:
        abort(404)
    return respond(
        url_for("user.card_detail", card_id=card_id),
        flash_msg="已收藏" if now_active else "已取消收藏",
        flash_cat="success" if now_active else "info",
        action="favorite",
        state=now_active,
        count=count,
    )


@user_bp.route("/user/<username>/follow", methods=["POST"])
@login_required
def user_follow(username):
    target = get_user_by_username(username)
    if not target or target.is_profile_banned:
        abort(404)
    now_following, error = toggle_user_follow(current_user, target)
    if error == "self":
        flash("不能关注自己", "warning")
    else:
        db.session.commit()
        if now_following:
            flash("已关注", "success")
        else:
            flash("已取消关注", "info")
    if is_xhr():
        # 局部提交：返回新状态供前端切换按钮，不整页刷新
        if error == "self":
            return jsonify({"ok": False, "error": "不能关注自己"})
        return jsonify({
            "ok": True,
            "action": "follow",
            "state": now_following,
        })
    return redirect(url_for("user.profile", username=username))


def _serialize_comment_author(cm):
    return {
        "id": cm.author.id,
        "username": cm.author.username,
        "display_name": cm.author.display_name,
        "avatar": cm.author.avatar or "",
    } if cm.author else {
        "id": cm.user_id,
        "username": "deleted",
        "display_name": "已注销用户",
        "avatar": "",
    }


@user_bp.route("/api/card/<card_id>/comments", methods=["GET"])
def card_comments_api(card_id):
    """返回角色卡评论 JSON，供前端抽屉 AJAX 分页加载。与 App 共用 card_comments_list。"""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    sort = request.args.get("sort", "latest")
    only_author = request.args.get("only_author") == "1"

    card, data, err_code = card_comments_list(
        card_id, current_user, page=page, per_page=per_page, sort=sort,
        only_author=only_author,
    )
    if err_code == "not_found":
        return jsonify({"error": "not found"}), 404
    pagination = data["pagination"]
    like_counts = data["like_counts"]
    user_liked_ids = data["user_liked_ids"]
    replies_by_parent = data["replies_by_parent"]
    reply_like_counts = data["reply_like_counts"]
    reply_liked_ids = data["reply_liked_ids"]

    # 楼层号只在「最新」排序且未筛选时展示（最热/只看作者下位置无意义，隐藏避免误导）
    show_floor = (sort == "latest") and not only_author

    def _serialize(cm, like_count, liked, idx=None, include_replies=True):
        item = {
            "id": cm.id,
            "content": cm.content,
            "image_data": cm.image_data,
            "created_at": (cm.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if cm.created_at else "",
            "author": _serialize_comment_author(cm),
            "can_report": (
                current_user.is_authenticated
                and cm.author is not None
                and cm.author.id != current_user.id
            ),
            "report_url": url_for("user.report", type="comment", id=cm.id),
            "is_author": (cm.user_id == card.author_id),
            "can_delete": (
                current_user.is_authenticated
                and (cm.user_id == current_user.id or current_user.is_super_admin)
            ),
            "delete_url": url_for("user.card_comment_delete", card_id=card_id, comment_id=cm.id),
            "floor": (pagination.total - ((page - 1) * per_page + idx)) if (show_floor and idx is not None) else None,
            "like_count": like_count,
            "liked": liked,
            "is_pinned": bool(cm.is_pinned),
            "can_pin": (
                current_user.is_authenticated
                and (card.author_id == current_user.id or current_user.is_super_admin)
            ),
            "reply_to": (
                {
                    "id": cm.reply_to.id,
                    "display_name": cm.reply_to.author.display_name if (cm.reply_to and cm.reply_to.author) else "未知用户",
                }
                if cm.reply_to
                else None
            ),
            "moderated": bool(cm.moderated),
            "is_mine": bool(current_user.is_authenticated and cm.user_id == current_user.id),
        }
        if include_replies:
            # 楼中楼最多两层：子回复不再向下嵌套。
            item["replies"] = [
                _serialize(
                    r,
                    reply_like_counts.get(r.id, 0),
                    r.id in reply_liked_ids,
                    include_replies=False,
                )
                for r in replies_by_parent.get(cm.id, [])
            ]
        else:
            # 子回复保持结构一致：replies 恒为 []。
            item["replies"] = []
        return item

    items = [
        _serialize(cm, like_counts.get(cm.id, 0), cm.id in user_liked_ids, idx=idx)
        for idx, cm in enumerate(pagination.items)
    ]

    # 定位指定评论所在分页：供「从外部带 comment 参数进入」时自动翻到对应评论
    focus_page = None
    focus_id = request.args.get("comment_id", type=int)
    if focus_id is not None:
        target = db.session.get(Comment, focus_id)
        if target and target.card_id == card_id:
            newer = (
                Comment.query.filter_by(card_id=card_id, is_hidden=False)
                .filter(Comment.created_at > target.created_at)
                .count()
            )
            same_newer = (
                Comment.query.filter_by(card_id=card_id, is_hidden=False)
                .filter(Comment.created_at == target.created_at, Comment.id > target.id)
                .count()
            )
            focus_page = (newer + same_newer) // per_page + 1

    return jsonify({
        "items": items,
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
        "has_next": pagination.has_next,
        "focus_page": focus_page,
    })


@user_bp.route("/card/<card_id>/comment", methods=["POST"])
@login_required
@block_if_muted(message="你已被禁言，暂时无法评论")
def card_comment(card_id):
    """发表评论：核心逻辑与 App 共用 create_comment 一个函数。"""
    db.get_or_404(Card, card_id)
    content = (request.form.get("content") or "").strip()
    reply_to_id = request.form.get("reply_to_id", type=int)

    # 图片上传：转成 base64 data URL 后交给共享函数
    image_data = None
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        ext = (
            image_file.filename.rsplit(".", 1)[-1].lower()
            if "." in image_file.filename
            else ""
        )
        if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
            err_msg = "仅支持上传 png/jpg/jpeg/gif/webp 格式的图片"
            if not is_xhr():
                flash(err_msg, "warning")
            return jsonify({"error": err_msg}), 400

        image_file.seek(0, os.SEEK_END)
        file_size = image_file.tell()
        image_file.seek(0)
        if file_size > 5 * 1024 * 1024:
            err_msg = "图片大小不能超过 5MB"
            if not is_xhr():
                flash(err_msg, "warning")
            return jsonify({"error": err_msg}), 400

        raw = image_file.read()
        try:
            image_data = raw_bytes_to_webp_data_url(raw, max_edge=1280, quality=80)
        except Exception as e:  # noqa: BLE001
            current_app.logger.error(f"评论图片转 WebP 失败: {e}")
            err_msg = "图片处理失败，请重试"
            if is_xhr():
                return jsonify({"error": err_msg}), 500
            flash(err_msg, "danger")
            return redirect(url_for("user.card_detail", card_id=card_id))

    _cm, err_code = create_comment(
        card_id, current_user, content,
        reply_to_id=reply_to_id, image_data=image_data,
    )
    if err_code == "empty":
        return respond(url_for("user.card_detail", card_id=card_id), ok=False, status=400,
                       flash_msg="评论内容不能为空", flash_cat="warning",
                       error="评论内容不能为空")
    if err_code == "too_long":
        return respond(url_for("user.card_detail", card_id=card_id), ok=False, status=400,
                       flash_msg="评论内容不能超过 500 字", flash_cat="warning",
                       error="评论内容不能超过 500 字")
    if err_code == "muted":
        return respond(url_for("user.card_detail", card_id=card_id), ok=False, status=403,
                       flash_msg="你已被禁言，暂时无法评论", flash_cat="warning",
                       error="你已被禁言，暂时无法评论")
    if err_code == "unauth":
        return respond(url_for("user.card_detail", card_id=card_id), ok=False, status=401,
                       flash_msg="请先登录后再评论", flash_cat="warning",
                       error="请先登录后再评论")
    return respond(url_for("user.card_detail", card_id=card_id),
                   flash_msg="评论成功", flash_cat="success")


@user_bp.route("/card/<card_id>/comment/<int:comment_id>/like", methods=["POST"])
@login_required
def card_comment_like(card_id, comment_id):
    cm = db.get_or_404(Comment, comment_id)
    if cm.card_id != card_id:
        abort(404)
    is_now_liked, new_count = toggle_comment_like(current_user, cm)
    return jsonify({"ok": True, "liked": is_now_liked, "count": new_count})


@user_bp.route("/card/<card_id>/comment/<int:comment_id>/pin", methods=["POST"])
@login_required
def card_comment_pin(card_id, comment_id):
    cm = db.get_or_404(Comment, comment_id)
    if cm.card_id != card_id:
        abort(404)
    err = pin_comment(current_user, cm)
    if err:
        abort(403, description=err)
    return jsonify({"ok": True, "is_pinned": cm.is_pinned})


@user_bp.route("/card/<card_id>/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def card_comment_delete(card_id, comment_id):
    cm = db.get_or_404(Comment, comment_id)
    if cm.card_id != card_id:
        abort(404)
    err = delete_comment(current_user, cm)
    if err:
        abort(403, description=err)
    return jsonify({"ok": True})


@user_bp.route("/my/card/<card_id>/resubmit", methods=["POST"])
@login_required
def card_resubmit(card_id):
    card = db.get_or_404(Card, card_id)
    if card.author_id != current_user.id:
        abort(404)
    error = resubmit_card(current_user, card)
    if error:
        flash(error, "warning")
    else:
        flash("已重新提交审核", "success")
    return respond(
        url_for("user.my_cards"),
        action="resubmit",
        status=card.status,
        status_html=STATUS_BADGE_HTML.get(card.status, ""),
    )


@user_bp.route("/my/card/<card_id>/toggle-hidden", methods=["POST"])
@login_required
def card_toggle_hidden(card_id):
    card = db.get_or_404(Card, card_id)
    if card.author_id != current_user.id:
        abort(404)
    error = toggle_card_hidden(current_user, card)
    if error:
        abort(404, description=error)
    return respond(
        url_for("user.my_cards"),
        flash_msg="已隐藏" if card.is_hidden else "已取消隐藏",
        flash_cat="success",
        action="hidden",
        state=card.is_hidden,
    )


@user_bp.route("/my/card/<card_id>/edit", methods=["GET", "POST"])
@login_required
def card_edit(card_id):
    card = db.get_or_404(Card, card_id)
    if card.author_id != current_user.id:
        abort(404)

    if request.method == "POST":
        tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
        dialogue_style = []
        try:
            ds_list = json.loads(request.form.get("dialogue_style_json") or "[]")
            if isinstance(ds_list, list):
                for item in ds_list:
                    if isinstance(item, dict):
                        dialogue_style.append(
                            {
                                "user": str(item.get("user") or ""),
                                "assistant": str(item.get("assistant") or ""),
                            }
                        )
        except json.JSONDecodeError:
            dialogue_style = []

        images = {}
        try:
            keep = json.loads(request.form.get("images_keep_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            keep = {}
        if not isinstance(keep, dict):
            keep = {}
        for slot in ("square", "landscape", "portrait"):
            f = request.files.get("image_" + slot)
            if f and f.filename:
                raw = f.read()
                if raw:
                    images[slot] = raw
                    continue
            if keep.get(slot):
                images[slot] = str(keep[slot])

        payload = {
            "name": request.form.get("name"),
            "gender": request.form.get("gender"),
            "persona": request.form.get("persona"),
            "intro": request.form.get("intro"),
            "opening": request.form.get("opening"),
            "original_link": request.form.get("original_link"),
            "cover_focus": request.form.get("cover_focus"),
            "author_note": request.form.get("author_note"),
            "author_note_interval": request.form.get("author_note_interval"),
            "tags": tags,
            "dialogue_style": dialogue_style,
            "images": images,
        }
        error = update_card_from_payload(card, payload)
        if error:
            flash(error, "warning")
            return redirect(url_for("user.card_edit", card_id=card.id))
        flash("角色卡已更新，已重新提交审核", "success")
        return redirect(url_for("user.my_cards"))

    # GET：构造预填数据
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = [
        {"user": d.user_text, "assistant": d.assistant_text}
        for d in CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
    ]
    images = load_card_images(card.id)
    prefill = {
        "id": card.id,
        "name": card.name,
        "gender": card.gender,
        "persona": card.persona,
        "intro": card.intro,
        "opening": card.opening,
        "tags": tags,
        "original_link": card.original_link or "",
        "cover_focus": card.cover_focus or "",
        "author_note": card.author_note or "",
        "author_note_interval": card.author_note_interval or 0,
    }
    return render_template(
        "publish/edit.html",
        prefill=prefill,
        dialogue_initial=dialogue,
        images_initial=images,
        action_url=url_for("user.card_edit", card_id=card.id),
    )


@user_bp.route("/notifications")
@login_required
def notifications():
    page = request.args.get("page", 1, type=int)
    pagination = notifications_page(current_user.id, page=page)
    unread = sum(1 for n in pagination.items if not n.is_read)
    return render_template(
        "user/notifications.html",
        items=pagination.items,
        pagination=pagination,
        args={},
        unread=unread,
    )


@user_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    from ..services.notification_service import mark_all_read

    mark_all_read(current_user.id)
    return respond(
        url_for("user.notifications"),
        flash_msg="已全部标记为已读", flash_cat="success",
        action="read_all",
    )


def _paginate_follows(u, kind, page):
    """分页返回某用户的粉丝/关注列表（User 对象），按关注时间倒序。"""
    if kind == "followers":
        link_col = UserFollow.following_id  # 被关注者 = u
        join_col = UserFollow.follower_id  # 要展示的是「粉丝」
    else:
        link_col = UserFollow.follower_id  # 关注者 = u
        join_col = UserFollow.following_id  # 要展示的是「已关注的人」
    query = (
        db.session.query(User)
        .join(UserFollow, join_col == User.id)
        .filter(link_col == u.id)
        .order_by(UserFollow.created_at.desc())
    )
    return query.paginate(page=page, per_page=20, error_out=False)


def _follow_items(users, include_banned):
    """组装列表项：附带「当前用户是否已关注该用户」。

    include_banned 为 False 时（他人访问），过滤掉被「禁止主页被访问」的用户。
    """
    if not include_banned:
        users = [user for user in users if not user.is_profile_banned]
    ids = [user.id for user in users]
    following_ids = set()
    if current_user.is_authenticated and ids:
        rows = UserFollow.query.filter(
            UserFollow.follower_id == current_user.id,
            UserFollow.following_id.in_(ids),
        ).all()
        following_ids = {r.following_id for r in rows}
    return [
        {"user": user, "is_following": user.id in following_ids} for user in users
    ]


def resolve_report_target(target_type, raw_id):
    """根据类型与原始 id 解析被举报对象，返回 (canonical_id, display, target_url) 或 None。

    委托给 report_service.describe_report_target，避免与后台解析逻辑重复实现。
    """
    d = describe_report_target(target_type, raw_id)
    if not d:
        return None
    return d["id"], d["display"], d["url"]


@user_bp.route("/report", methods=["GET", "POST"])
@login_required
def report():
    target_type = (request.values.get("type") or "").strip()
    raw_id = (request.values.get("id") or "").strip()
    if target_type not in REPORT_TARGETS:
        abort(400)

    resolved = resolve_report_target(target_type, raw_id)
    if not resolved:
        flash("举报对象不存在", "warning")
        return redirect(url_for("main.index"))
    canonical_id, display, target_url = resolved

    # 不能举报自己
    if target_type == "user":
        from ..models import User

        target_user = db.session.get(User, int(canonical_id))
        if target_user and target_user.id == current_user.id:
            flash("不能举报自己", "warning")
            return redirect(target_url)

    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        detail = (request.form.get("detail") or "").strip()
        ok_flag, msg = submit_report(
            current_user, target_type, canonical_id, reason, detail, display
        )
        if ok_flag:
            flash("举报已提交，管理员会尽快处理", "success")
        else:
            flash(msg, "info" if "已经举报" in msg else "warning")
        return redirect(target_url)

    return render_template(
        "user/report.html",
        target_type=target_type,
        display=display,
        target_url=target_url,
        raw_id=raw_id,
        reasons=REPORT_REASONS,
    )


# ---------------- 我的工单 ----------------
def _ticket_image_data():
    """从上传的 image 文件读取并压缩为 WebP data URL；无文件返回 None，出错返回错误消息。"""
    f = request.files.get("image")
    if not f or not f.filename:
        return None
    raw = f.read()
    if not raw:
        return "图片内容为空"
    if len(raw) > 16 * 1024 * 1024:
        return "图片过大，请控制在 16MB 以内"
    try:
        from ..services.image_service import raw_bytes_to_webp_data_url

        return raw_bytes_to_webp_data_url(raw, max_edge=1280, quality=82)
    except Exception:
        return "图片格式无法识别或处理失败"


@user_bp.route("/my/tickets")
@login_required
def my_tickets():
    """当前用户的工单列表（按最新消息时间倒序）。"""
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "").strip()
    st = request.args.get("status", "all")

    base = Ticket.query.filter_by(user_id=current_user.id)
    if st in TICKET_STATUSES:
        base = base.filter(Ticket.status == st)
    if q:
        base = base.filter(Ticket.title.ilike(f"%{q}%"))
    # 按 updated_at 倒序（未更新则按 created_at）
    tickets = base.order_by(
        db.func.coalesce(Ticket.updated_at, Ticket.created_at).desc()
    ).paginate(page=page, per_page=20, error_out=False)

    return render_template(
        "user/tickets.html",
        tickets=tickets,
        q=q,
        st=st,
    )


@user_bp.route("/my/tickets/new", methods=["GET", "POST"])
@login_required
def my_tickets_new():
    """新建工单。"""
    categories = (
        TicketCategory.query.filter_by(enabled=True)
        .order_by(TicketCategory.sort_order, TicketCategory.name)
        .all()
    )

    if request.method == "POST":
        cat_id = request.form.get("category_id", type=int)
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        image_data = _ticket_image_data()
        if isinstance(image_data, str):
            flash(image_data, "warning")
            return redirect(url_for("user.my_tickets_new"))

        if not title:
            flash("请填写工单主题", "warning")
            return redirect(url_for("user.my_tickets_new"))
        if not content and not image_data:
            flash("请填写工单内容或上传图片", "warning")
            return redirect(url_for("user.my_tickets_new"))

        t = Ticket(
            user_id=current_user.id,
            category_id=cat_id or None,
            title=title,
            content=content or "",
        )
        db.session.add(t)
        db.session.flush()
        # 第一条消息即工单内容
        tm = TicketMessage(
            ticket_id=t.id,
            sender_id=current_user.id,
            sender_role=MSG_ROLE_USER,
            content=content or "",
            image_data=image_data,
        )
        db.session.add(tm)
        db.session.commit()
        notify_super_admins(
            f"新工单「{title}」来自 {current_user.nickname or current_user.username}",
            type_="ticket",
        )
        flash("工单已提交，请等待管理员回复", "success")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))

    return render_template("user/ticket_new.html", categories=categories)


@user_bp.route("/my/tickets/<int:ticket_id>")
@login_required
def my_tickets_detail(ticket_id):
    """工单详情（对话视图）。"""
    t = db.get_or_404(Ticket, ticket_id)
    if t.user_id != current_user.id and not current_user.is_super_admin:
        abort(404)
    return render_template("user/ticket_detail.html", ticket=t)


@user_bp.route("/my/tickets/<int:ticket_id>/reply", methods=["POST"])
@login_required
def my_tickets_reply(ticket_id):
    """用户回复工单。"""
    t = db.get_or_404(Ticket, ticket_id)
    if t.user_id != current_user.id:
        abort(404)
    if t.status == TICKET_CLOSED:
        flash("工单已关闭，无法回复", "warning")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))

    content = (request.form.get("content") or "").strip()
    image_data = _ticket_image_data()
    if isinstance(image_data, str):
        flash(image_data, "warning")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))
    if not content and not image_data:
        flash("请填写回复内容或上传图片", "warning")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))

    tm = TicketMessage(
        ticket_id=t.id,
        sender_id=current_user.id,
        sender_role=MSG_ROLE_USER,
        content=content,
        image_data=image_data,
    )
    db.session.add(tm)
    t.status = TICKET_OPEN
    db.session.commit()
    notify_super_admins(
        f"工单「{t.title}」有新回复（{current_user.nickname or current_user.username}）",
        type_="ticket",
    )
    flash("回复已发送", "success")
    return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))


@user_bp.route("/my/tickets/<int:ticket_id>/close", methods=["POST"])
@login_required
def my_tickets_close(ticket_id):
    """用户关闭工单。"""
    t = db.get_or_404(Ticket, ticket_id)
    if t.user_id != current_user.id:
        abort(404)
    if t.status == TICKET_CLOSED:
        flash("工单已关闭", "info")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))
    t.status = TICKET_CLOSED
    t.closed_at = db.func.now()
    db.session.commit()
    flash("工单已关闭", "success")
    return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))


@user_bp.route("/my/tickets/<int:ticket_id>/reopen", methods=["POST"])
@login_required
def my_tickets_reopen(ticket_id):
    """用户重新打开已关闭的工单。"""
    t = db.get_or_404(Ticket, ticket_id)
    if t.user_id != current_user.id:
        abort(404)
    if t.status != TICKET_CLOSED:
        flash("工单未关闭，无需重新打开", "info")
        return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))
    t.status = TICKET_OPEN
    t.closed_at = None
    db.session.commit()
    notify_super_admins(
        f"工单「{t.title}」已由用户重新打开",
        type_="ticket",
    )
    flash("工单已重新打开", "success")
    return redirect(url_for("user.my_tickets_detail", ticket_id=t.id))
