import json

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import (
    Card,
    CardDialogueStyle,
    CardFavorite,
    CardImage,
    CardLike,
    CardTag,
    Comment,
    Notification,
    Report,
    UserFollow,
)
from ..services.notification_service import notify

user_bp = Blueprint("user", __name__)

REPORT_TARGETS = ("card", "comment", "user")
REPORT_REASONS = [
    ("spam", "垃圾广告 / 刷屏"),
    ("porn", "色情低俗"),
    ("violence", "暴力血腥"),
    ("politics", "违规政治内容"),
    ("abuse", "人身攻击 / 辱骂"),
    ("copyright", "侵犯版权"),
    ("other", "其他"),
]


@user_bp.route("/user/<username>")
def profile(username):
    u = User_query_by_username(username)
    if not u or u.is_banned:
        abort(404)
    page = request.args.get("page", 1, type=int)
    pagination = (
        Card.query.filter_by(author_id=u.id)
        .filter(Card.is_hidden.is_(False))
        .order_by(Card.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
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
        cards=pagination.items,
        pagination=pagination,
        args={"username": username},
        is_self=(current_user.is_authenticated and current_user.id == u.id),
        follower_count=follower_count,
        following_count=following_count,
        is_following=is_following,
    )


@user_bp.route("/card/<card_id>")
def card_detail(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)

    author = card.author
    is_owner = current_user.is_authenticated and current_user.id == card.author_id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    is_public = (
        card.status == "approved"
        and not card.is_hidden
        and not (author and author.is_banned)
    )

    # 隐藏卡、未通过审核的卡、以及被封禁作者的卡，仅对作者与管理员可见
    if not (is_public or is_owner or is_admin):
        abort(404)

    if not is_owner:
        card.view_count = (card.view_count or 0) + 1
        db.session.commit()

    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = [
        {"user": d.user_text, "assistant": d.assistant_text}
        for d in CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
    ]
    images = {i.slot: i.data for i in CardImage.query.filter_by(card_id=card.id).all()}

    like_count = CardLike.query.filter_by(card_id=card.id).count()
    favorite_count = CardFavorite.query.filter_by(card_id=card.id).count()
    comments = (
        Comment.query.filter_by(card_id=card.id)
        .order_by(Comment.created_at.asc())
        .all()
    )
    liked = (
        current_user.is_authenticated
        and CardLike.query.filter_by(user_id=current_user.id, card_id=card.id).first()
        is not None
    )
    favorited = (
        current_user.is_authenticated
        and CardFavorite.query.filter_by(user_id=current_user.id, card_id=card.id).first()
        is not None
    )
    following = (
        current_user.is_authenticated
        and author is not None
        and UserFollow.query.filter_by(
            follower_id=current_user.id, following_id=author.id
        ).first()
        is not None
    )
    return render_template(
        "user/card_detail.html",
        card=card,
        author=author,
        tags=tags,
        dialogue=dialogue,
        images=images,
        is_owner=is_owner,
        is_admin=is_admin,
        like_count=like_count,
        favorite_count=favorite_count,
        comment_count=len(comments),
        comments=comments,
        liked=liked,
        favorited=favorited,
        following=following,
    )


@user_bp.route("/my/cards")
@login_required
def my_cards():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Card.query.filter_by(author_id=current_user.id)
        .order_by(Card.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    stats = dict(
        db.session.query(Card.status, func.count(Card.id))
        .filter(Card.author_id == current_user.id)
        .group_by(Card.status)
        .all()
    )
    return render_template(
        "user/my_cards.html",
        cards=pagination.items,
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
    pagination = (
        Card.query.join(CardFavorite)
        .filter(CardFavorite.user_id == current_user.id)
        .order_by(CardFavorite.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    return render_template(
        "user/card_list.html",
        cards=pagination.items,
        pagination=pagination,
        args={},
        title="我的收藏",
    )


@user_bp.route("/my/likes")
@login_required
def my_likes():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Card.query.join(CardLike)
        .filter(CardLike.user_id == current_user.id)
        .order_by(CardLike.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    return render_template(
        "user/card_list.html",
        cards=pagination.items,
        pagination=pagination,
        args={},
        title="我点赞的",
    )


@user_bp.route("/card/<card_id>/like", methods=["POST"])
@login_required
def card_like(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    existing = CardLike.query.filter_by(user_id=current_user.id, card_id=card_id).first()
    if existing:
        db.session.delete(existing)
        flash("已取消点赞", "info")
    else:
        db.session.add(CardLike(user_id=current_user.id, card_id=card_id))
        flash("已点赞", "success")
    db.session.commit()
    return redirect(url_for("user.card_detail", card_id=card_id))


@user_bp.route("/card/<card_id>/favorite", methods=["POST"])
@login_required
def card_favorite(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    existing = CardFavorite.query.filter_by(user_id=current_user.id, card_id=card_id).first()
    if existing:
        db.session.delete(existing)
        flash("已取消收藏", "info")
    else:
        db.session.add(CardFavorite(user_id=current_user.id, card_id=card_id))
        flash("已收藏", "success")
    db.session.commit()
    return redirect(url_for("user.card_detail", card_id=card_id))


@user_bp.route("/user/<username>/follow", methods=["POST"])
@login_required
def user_follow(username):
    target = User_query_by_username(username)
    if not target or target.is_banned:
        abort(404)
    if str(target.id) == str(current_user.get_id()):
        flash("不能关注自己", "warning")
    else:
        existing = UserFollow.query.filter_by(
            follower_id=current_user.id, following_id=target.id
        ).first()
        if existing:
            db.session.delete(existing)
            flash("已取消关注", "info")
        else:
            db.session.add(
                UserFollow(follower_id=current_user.id, following_id=target.id)
            )
            notify(target.id, f"{current_user.nickname} 关注了你", type_="follow")
            flash("已关注", "success")
        db.session.commit()
    return redirect(url_for("user.profile", username=username))


@user_bp.route("/card/<card_id>/comment", methods=["POST"])
@login_required
def card_comment(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("评论内容不能为空", "warning")
    else:
        db.session.add(
            Comment(card_id=card_id, user_id=current_user.id, content=content)
        )
        db.session.commit()
        flash("评论成功", "success")
    return redirect(url_for("user.card_detail", card_id=card_id))


@user_bp.route("/my/card/<card_id>/resubmit", methods=["POST"])
@login_required
def card_resubmit(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.author_id != current_user.id:
        abort(404)
    if card.status != "rejected":
        flash("仅被拒绝的角色卡可以重新提审", "warning")
    else:
        card.status = "pending"
        db.session.commit()
        flash("已重新提交审核", "success")
    return redirect(url_for("user.my_cards"))


@user_bp.route("/my/card/<card_id>/toggle-hidden", methods=["POST"])
@login_required
def card_toggle_hidden(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.author_id != current_user.id:
        abort(404)
    card.is_hidden = not card.is_hidden
    db.session.commit()
    flash("已隐藏" if card.is_hidden else "已取消隐藏", "success")
    return redirect(url_for("user.my_cards"))


@user_bp.route("/my/card/<card_id>/edit", methods=["GET", "POST"])
@login_required
def card_edit(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.author_id != current_user.id:
        abort(404)

    if request.method == "POST":
        card.name = (request.form.get("name") or "").strip() or card.name
        card.gender = request.form.get("gender") or card.gender
        card.persona = request.form.get("persona") or ""
        card.intro = request.form.get("intro") or ""
        card.opening = request.form.get("opening") or ""
        card.original_link = request.form.get("original_link") or None
        card.status = "pending"  # 编辑后自动重新提审

        # 标签覆盖式更新
        CardTag.query.filter_by(card_id=card.id).delete()
        for t in [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]:
            db.session.add(CardTag(card_id=card.id, tag=t))

        # 对话风格覆盖式更新
        CardDialogueStyle.query.filter_by(card_id=card.id).delete()
        try:
            ds_list = json.loads(request.form.get("dialogue_style_json") or "[]")
        except json.JSONDecodeError:
            ds_list = []
        for idx, item in enumerate(ds_list):
            if isinstance(item, dict):
                db.session.add(
                    CardDialogueStyle(
                        card_id=card.id,
                        turn_index=idx,
                        user_text=item.get("user") or "",
                        assistant_text=item.get("assistant") or "",
                    )
                )

        # 图片覆盖式更新
        CardImage.query.filter_by(card_id=card.id).delete()
        try:
            img_dict = json.loads(request.form.get("images_json") or "{}")
        except json.JSONDecodeError:
            img_dict = {}
        for slot in ("square", "landscape", "portrait"):
            if img_dict.get(slot):
                db.session.add(
                    CardImage(card_id=card.id, slot=slot, data=str(img_dict[slot]))
                )

        db.session.commit()
        flash("角色卡已更新，已重新提交审核", "success")
        return redirect(url_for("user.my_cards"))

    # GET：构造预填数据
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = [
        {"user": d.user_text, "assistant": d.assistant_text}
        for d in CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
    ]
    images = {i.slot: i.data for i in CardImage.query.filter_by(card_id=card.id).all()}
    prefill = {
        "id": card.id,
        "name": card.name,
        "gender": card.gender,
        "persona": card.persona,
        "intro": card.intro,
        "opening": card.opening,
        "tags": tags,
        "original_link": card.original_link or "",
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
    pagination = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .paginate(page=page, per_page=20, error_out=False)
    )
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
    flash("已全部标记为已读", "success")
    return redirect(url_for("user.notifications"))


def User_query_by_username(username):
    from ..models import User

    return User.query.filter_by(username=username).first()


def resolve_report_target(target_type, raw_id):
    """根据类型与原始 id 解析被举报对象，返回 (canonical_id, display, target_url) 或 None。"""
    from ..models import Comment, User

    if target_type == "card":
        card = db.session.get(Card, raw_id)
        if not card:
            return None
        return (
            str(card.id),
            card.name,
            url_for("user.card_detail", card_id=card.id),
        )
    if target_type == "comment":
        comment = db.session.get(Comment, raw_id)
        if not comment:
            return None
        return (
            str(comment.id),
            comment.content[:30],
            url_for("user.card_detail", card_id=comment.card_id),
        )
    if target_type == "user":
        u = User_query_by_username(raw_id)
        if not u:
            try:
                u = db.session.get(User, int(raw_id))
            except (TypeError, ValueError):
                u = None
        if not u:
            return None
        return (
            str(u.id),
            u.nickname,
            url_for("user.profile", username=u.username),
        )
    return None


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
        valid_reasons = {r[0] for r in REPORT_REASONS}
        if reason not in valid_reasons:
            flash("请选择举报原因", "warning")
        elif Report.query.filter_by(
            reporter_id=current_user.id,
            target_type=target_type,
            target_id=canonical_id,
            status="pending",
        ).first():
            flash("你已经举报过该对象，请勿重复提交", "info")
            return redirect(target_url)
        else:
            db.session.add(
                Report(
                    reporter_id=current_user.id,
                    target_type=target_type,
                    target_id=canonical_id,
                    reason=reason,
                    detail=detail,
                )
            )
            db.session.commit()
            # 通知所有超级管理员
            from ..models import User

            for admin in User.query.filter_by(role="super_admin").all():
                notify(
                    admin.id,
                    f'收到一条对{target_type}的举报：{display}',
                    type_="report",
                )
            db.session.commit()
            flash("举报已提交，管理员会尽快处理", "success")
            return redirect(target_url)

    return render_template(
        "user/report.html",
        target_type=target_type,
        display=display,
        target_url=target_url,
        raw_id=raw_id,
        reasons=REPORT_REASONS,
    )
