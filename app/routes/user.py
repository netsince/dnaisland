import json
import re

from datetime import datetime
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
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
    Punishment,
    Report,
    User,
    UserFollow,
)
from ..models.punishment import (
    APPEAL_ACCEPTED,
    APPEAL_PENDING,
    APPEAL_REJECTED,
    PUNISHMENT_TYPES,
)
from ..services.notification_service import notify
from ..services.image_service import compress_image, crop_square_and_compress
from ..services.card_service import (
    attach_covers,
    build_export_package,
    load_card_images,
)

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
    if not u:
        abort(404)
    is_self = current_user.is_authenticated and current_user.id == u.id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    # 禁止主页被访问：他人仅可见受限提示与处罚列表；本人与管理员可见完整主页
    restricted = (not is_self) and (not is_admin) and u.is_profile_banned

    page = request.args.get("page", 1, type=int)
    if is_self or is_admin:
        card_query = Card.query.filter_by(author_id=u.id)
    else:
        # 信息层面过滤：被「屏蔽全部角色卡」作者的卡片对他人不可见
        card_query = Card.visible_to(current_user).filter(Card.author_id == u.id)
    pagination = card_query.order_by(Card.created_at.desc()).paginate(
        page=page, per_page=12, error_out=False
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
        cards=attach_covers(pagination.items),
        pagination=pagination,
        args={"username": username},
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
    u = User_query_by_username(username)
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
    u = User_query_by_username(username)
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
        u.nickname = (request.form.get("nickname") or "").strip() or u.nickname
        u.bio = (request.form.get("bio") or "").strip()
        u.location = (request.form.get("location") or "").strip()

        website = (request.form.get("website") or "").strip()
        if website:
            if not re.match(r"^https?://", website):
                website = "https://" + website
            u.website = website
        else:
            u.website = None

        birthday_raw = (request.form.get("birthday") or "").strip()
        if birthday_raw:
            try:
                u.birthday = datetime.strptime(birthday_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("生日格式不正确，应为 YYYY-MM-DD", "warning")
                return render_template("user/profile_edit.html", u=u)
        else:
            u.birthday = None

        # 头像：移除 / 裁剪后上传 / 保留原值
        if request.form.get("remove_avatar"):
            u.avatar = None
        else:
            avatar_data = (request.form.get("avatar_data") or "").strip()
            if avatar_data:
                try:
                    u.avatar = crop_square_and_compress(avatar_data)
                except Exception:
                    flash("头像处理失败，请重试", "warning")
                    return render_template("user/profile_edit.html", u=u)

        db.session.commit()
        flash("个人资料已更新", "success")
        return redirect(url_for("user.profile", username=u.username))

    return render_template("user/profile_edit.html", u=u)


@user_bp.route("/my/punishments")
@login_required
def my_punishments():
    from ..models import User as _User

    items = (
        Punishment.query.filter_by(user_id=current_user.id)
        .order_by(Punishment.created_at.desc())
        .all()
    )
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
    from ..models import User as _User

    p = db.session.get(Punishment, punishment_id)
    if not p or p.user_id != current_user.id:
        abort(404)
    if not p.can_appeal:
        flash("该处罚不可申诉或你已提交过申诉", "warning")
        return redirect(url_for("user.my_punishments"))
    reason = (request.form.get("appeal_reason") or "").strip()
    if not reason:
        flash("请填写申诉理由", "warning")
        return redirect(url_for("user.my_punishments"))
    p.appealed = True
    p.appeal_reason = reason
    p.appeal_status = APPEAL_PENDING
    p.appeal_at = db.func.now()
    db.session.commit()
    for admin in _User.query.filter_by(role="super_admin").all():
        notify(
            admin.id,
            f'用户 {current_user.nickname} 对处罚「{PUNISHMENT_TYPES.get(p.type, p.type)}」提交了申诉，请到「处罚申诉」处理。',
            type_="punish",
        )
    db.session.commit()
    flash("申诉已提交，等待管理员处理（仅可申诉一次）", "success")
    return redirect(url_for("user.my_punishments"))


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
        and not (author and (author.is_cards_hidden or author.is_profile_banned))
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
    images = load_card_images(card.id)

    like_count = CardLike.query.filter_by(card_id=card.id).count()
    favorite_count = CardFavorite.query.filter_by(card_id=card.id).count()
    comments = (
        Comment.query.filter_by(card_id=card.id)
        .order_by(Comment.created_at.asc())
        .all()
    )
    # 屏蔽全部评论：被处罚用户的评论对他人不可见（本人与管理员可见）；
    # 被审核拒绝（is_hidden）的评论同样对他人不可见
    visible_comments = [
        c
        for c in comments
        if (not c.is_hidden) and not (
            c.author
            and c.author.is_comments_hidden
            and not (current_user.is_authenticated and current_user.id == c.author.id)
            and not is_admin
        )
    ]
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
        comment_count=len(visible_comments),
        comments=visible_comments,
        liked=liked,
        favorited=favorited,
        following=following,
    )


@user_bp.route("/card/<card_id>/export")
def card_export(card_id):
    """导出角色卡为 dna-client 可识别的 JSON 下载。

    可见性与 card_detail 一致：仅对已通过且未隐藏的公开卡、作者本人或管理员开放。
    仅登录用户可复制。
    """
    if not current_user.is_authenticated:
        return jsonify(
            error="请先登录后再复制角色卡",
            login_url=url_for("auth.login"),
        ), 401

    card = db.session.get(Card, card_id)
    if not card:
        abort(404)

    author = card.author
    is_owner = current_user.is_authenticated and current_user.id == card.author_id
    is_admin = current_user.is_authenticated and current_user.is_super_admin
    is_public = (
        card.status == "approved"
        and not card.is_hidden
        and not (author and (author.is_cards_hidden or author.is_profile_banned))
    )
    if not (is_public or is_owner or is_admin):
        abort(404)

    # 收集复制者上下文，用于版权溯源（cp/pd/up/ct/ci）
    origin = request.host_url.rstrip("/")
    copier = current_user.username if current_user.is_authenticated else "anonymous"
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
    body = json.dumps(package, ensure_ascii=False, indent=2)
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
        cards=attach_covers(pagination.items),
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
        Card.visible_to(current_user)
        .join(CardFavorite)
        .filter(CardFavorite.user_id == current_user.id)
        .order_by(CardFavorite.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    return render_template(
        "user/card_list.html",
        cards=attach_covers(pagination.items),
        pagination=pagination,
        args={},
        title="我的收藏",
    )


@user_bp.route("/my/likes")
@login_required
def my_likes():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Card.visible_to(current_user)
        .join(CardLike)
        .filter(CardLike.user_id == current_user.id)
        .order_by(CardLike.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    return render_template(
        "user/card_list.html",
        cards=attach_covers(pagination.items),
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
    if not target or target.is_profile_banned:
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
    if current_user.is_muted:
        flash("你已被禁言，暂时无法评论", "warning")
        return redirect(url_for("user.card_detail", card_id=card_id))
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
                    CardImage(
                        card_id=card.id,
                        slot=slot,
                        data=compress_image(str(img_dict[slot])),
                    )
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
