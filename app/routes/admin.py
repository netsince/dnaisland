from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func

from ..decorators import super_admin_required
from ..extensions import db
from ..models import (
    Card,
    CardDialogueStyle,
    CardImage,
    CardTag,
    Comment,
    Notification,
    Punishment,
    Report,
    User,
)
from ..models.punishment import (
    APPEAL_ACCEPTED,
    APPEAL_PENDING,
    APPEAL_REJECTED,
    PUNISHMENT_TYPES,
)
from ..services.notification_service import notify

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------- 仪表盘 / 入口 ----------------
@admin_bp.route("/")
@super_admin_required
def index():
    return redirect(url_for("admin.review"))


# ---------------- 用户管理 ----------------
@admin_bp.route("/users")
@super_admin_required
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    status = request.args.get("status", "").strip()
    verified = request.args.get("verified", "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(User.username.like(like), User.nickname.like(like), User.email.like(like))
        )
    if role:
        query = query.filter(User.role == role)
    if status:
        query = query.filter(User.status == status)
    if verified == "yes":
        query = query.filter(User.email_verified.is_(True))
    elif verified == "no":
        query = query.filter(User.email_verified.is_(False))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template(
        "admin/users.html",
        users=pagination.items,
        pagination=pagination,
        args={"q": q, "role": role, "status": status, "verified": verified},
        q=q,
        role=role,
        status=status,
        verified=verified,
    )


@admin_bp.route("/users/create", methods=["GET", "POST"])
@super_admin_required
def user_create():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        nickname = (request.form.get("nickname") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "user"
        status = request.form.get("status") or "active"
        if not (username and nickname and email and password):
            flash("请填写用户名、昵称、邮箱和密码", "danger")
            return render_template("admin/user_form.html", user=None)
        if User.query.filter_by(username=username).first():
            flash("用户名已存在", "danger")
            return render_template("admin/user_form.html", user=None)
        if User.query.filter_by(email=email).first():
            flash("邮箱已存在", "danger")
            return render_template("admin/user_form.html", user=None)
        if role not in ("user", "super_admin"):
            role = "user"
        u = User(username=username, nickname=nickname, email=email, role=role, status=status)
        u.verified = request.form.get("verified") == "1"
        label = (request.form.get("verified_label") or "").strip()
        u.verified_label = label or None
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("用户已创建", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@super_admin_required
def user_edit(user_id):
    u = db.session.get(User, user_id)
    if not u:
        abort(404)
    if request.method == "POST":
        u.username = (request.form.get("username") or "").strip() or u.username
        u.nickname = (request.form.get("nickname") or "").strip() or u.nickname
        u.email = (request.form.get("email") or "").strip() or u.email
        u.role = request.form.get("role") or u.role
        if u.role not in ("user", "super_admin"):
            u.role = "user"
        u.status = request.form.get("status") or u.status
        u.verified = request.form.get("verified") == "1"
        label = (request.form.get("verified_label") or "").strip()
        u.verified_label = label or None
        new_pwd = request.form.get("password") or ""
        if new_pwd:
            u.set_password(new_pwd)
        db.session.commit()
        flash("用户已更新", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=u)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@super_admin_required
def user_delete(user_id):
    u = db.session.get(User, user_id)
    if not u:
        abort(404)
    if u.id == current_user.id:
        flash("不能删除当前登录的账号", "danger")
        return redirect(url_for("admin.users"))
    db.session.delete(u)
    db.session.commit()
    flash("用户已删除", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/punish", methods=["GET", "POST"])
@super_admin_required
def user_punish(user_id):
    u = db.session.get(User, user_id)
    if not u:
        abort(404)

    if request.method == "POST":
        selected = request.form.getlist("types")
        reason = (request.form.get("reason") or "").strip()
        valid = [t for t in selected if t in PUNISHMENT_TYPES]
        if not valid:
            flash("请至少选择一项处罚", "warning")
            return redirect(url_for("admin.user_punish", user_id=user_id))

        active = {p.type for p in u.active_punishments}
        applied = []
        for ptype in valid:
            if ptype in active:
                continue
            p = Punishment(
                user_id=u.id,
                type=ptype,
                reason=reason,
                handled_by=current_user.id,
            )
            db.session.add(p)
            applied.append(ptype)
        # 施加副作用（重置资料 / 清除头像）
        for ptype in valid:
            if ptype == "reset_profile":
                u.nickname = f"UID{u.id}"
                u.bio = None
                u.location = None
                u.website = None
                u.birthday = None
            elif ptype == "clear_avatar":
                u.avatar = None
        db.session.commit()

        if applied:
            summary = "、".join(PUNISHMENT_TYPES[t] for t in applied)
            notify(
                u.id,
                f"你被平台施加了以下处罚：{summary}。可在「我的处罚」中查看与申诉。",
                type_="punish",
            )
            db.session.commit()
            flash(f'已对用户"{u.username}"施加处罚：{summary}', "success")
        else:
            flash("所选处罚均已生效，未做改动", "info")
        return redirect(url_for("admin.user_punish", user_id=user_id))

    return render_template(
        "admin/user_punish.html",
        u=u,
        punishment_types=PUNISHMENT_TYPES,
        active=u.active_punishments,
    )


@admin_bp.route("/punish/<int:punishment_id>/revoke", methods=["POST"])
@super_admin_required
def punish_revoke(punishment_id):
    p = db.session.get(Punishment, punishment_id)
    if not p:
        abort(404)
    if p.is_active:
        p.status = "revoked"
        db.session.commit()
        notify(
            p.user_id,
            f"你的一项处罚已被解除：{PUNISHMENT_TYPES.get(p.type, p.type)}。",
            type_="punish",
        )
        db.session.commit()
        flash("已解除该处罚", "success")
    return redirect(url_for("admin.user_punish", user_id=p.user_id))


@admin_bp.route("/punish/appeals")
@super_admin_required
def punish_appeals():
    items = (
        Punishment.query.filter_by(appeal_status=APPEAL_PENDING)
        .order_by(Punishment.appeal_at.asc())
        .all()
    )
    return render_template("admin/punish_appeals.html", items=items)


@admin_bp.route("/punish/<int:punishment_id>/appeal-resolve", methods=["POST"])
@super_admin_required
def punish_appeal_resolve(punishment_id):
    p = db.session.get(Punishment, punishment_id)
    if not p or p.appeal_status != APPEAL_PENDING:
        abort(404)
    action = (request.form.get("action") or "").strip()
    reply = (request.form.get("reply") or "").strip()

    if action == "accept":
        p.status = "revoked"
        p.appeal_status = APPEAL_ACCEPTED
        p.appeal_handled_at = db.func.now()
        p.appeal_handled_by = current_user.id
        p.appeal_reply = reply
        db.session.commit()
        notify(
            p.user_id,
            f"你的申诉已通过，处罚「{PUNISHMENT_TYPES.get(p.type, p.type)}」已解除。",
            type_="punish",
        )
        flash("已通过该申诉并解除处罚", "success")
    elif action == "reject":
        p.appeal_status = APPEAL_REJECTED
        p.appeal_handled_at = db.func.now()
        p.appeal_handled_by = current_user.id
        p.appeal_reply = reply
        db.session.commit()
        notify(
            p.user_id,
            f"你的申诉未通过。"
            + (f"管理员回复：{reply}" if reply else ""),
            type_="punish",
        )
        flash("已驳回该申诉", "success")
    else:
        flash("无效操作", "warning")
    return redirect(url_for("admin.punish_appeals"))


# ---------------- 角色卡管理 ----------------
@admin_bp.route("/cards")
@super_admin_required
def cards():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    gender = request.args.get("gender", "").strip()
    author = request.args.get("author", "").strip()
    query = Card.query
    if q:
        query = query.filter(Card.name.like(f"%{q}%"))
    if status:
        query = query.filter(Card.status == status)
    if gender:
        query = query.filter(Card.gender == gender)
    if author:
        like = f"%{author}%"
        query = query.join(User).filter(User.username.like(like))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Card.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template(
        "admin/cards.html",
        cards=pagination.items,
        pagination=pagination,
        args={"q": q, "status": status, "gender": gender, "author": author},
        q=q,
        status=status,
        gender=gender,
        author=author,
    )


@admin_bp.route("/cards/<card_id>/edit", methods=["GET", "POST"])
@super_admin_required
def card_edit(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    if request.method == "POST":
        card.name = (request.form.get("name") or "").strip() or card.name
        card.gender = request.form.get("gender") or card.gender
        card.persona = request.form.get("persona") or ""
        card.intro = request.form.get("intro") or ""
        card.opening = request.form.get("opening") or ""
        card.status = request.form.get("status") or card.status
        # 标签覆盖式更新
        CardTag.query.filter_by(card_id=card.id).delete()
        for t in [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]:
            db.session.add(CardTag(card_id=card.id, tag=t))
        db.session.commit()
        flash("角色卡已更新", "success")
        return redirect(url_for("admin.cards"))
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    return render_template("admin/card_form.html", card=card, tags=", ".join(tags))


@admin_bp.route("/cards/<card_id>/delete", methods=["POST"])
@super_admin_required
def card_delete(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    CardTag.query.filter_by(card_id=card.id).delete()
    CardDialogueStyle.query.filter_by(card_id=card.id).delete()
    CardImage.query.filter_by(card_id=card.id).delete()
    db.session.delete(card)
    db.session.commit()
    flash("角色卡已删除", "success")
    return redirect(url_for("admin.cards"))


# ---------------- 审核 ----------------
@admin_bp.route("/review")
@super_admin_required
def review():
    status = request.args.get("status", "pending").strip() or "pending"
    query = Card.query
    if status != "all":
        query = query.filter(Card.status == status)
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Card.created_at.asc()).paginate(
        page=page, per_page=20, error_out=False
    )
    # 统计各状态数量
    stats = dict(
        db.session.query(Card.status, func.count(Card.id)).group_by(Card.status).all()
    )
    return render_template(
        "admin/review_list.html",
        cards=pagination.items,
        pagination=pagination,
        args={"status": status},
        status=status,
        stats=stats,
    )


@admin_bp.route("/review/<card_id>")
@super_admin_required
def review_detail(card_id):
    card = db.session.get(Card, card_id)
    if not card:
        abort(404)
    author = db.session.get(User, card.author_id)
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = (
        CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
        .all()
    )
    images = {i.slot: i.data for i in CardImage.query.filter_by(card_id=card.id).all()}
    return render_template(
        "admin/review_detail.html",
        card=card,
        author=author,
        tags=tags,
        dialogue=dialogue,
        images=images,
    )


@admin_bp.route("/review/<card_id>/approve", methods=["POST"])
@super_admin_required
def review_approve(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.status == "approved":
        return redirect(url_for("admin.review"))
    card.status = "approved"
    db.session.commit()
    notify(
        card.author_id,
        f'你的角色卡"{card.name}"已通过审核并发布。',
        type_="review",
        related_card_id=card.id,
    )
    db.session.commit()
    flash(f'已通过："{card.name}"', "success")
    return redirect(url_for("admin.review"))


@admin_bp.route("/review/<card_id>/reject", methods=["POST"])
@super_admin_required
def review_reject(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.status == "rejected":
        return redirect(url_for("admin.review"))
    card.status = "rejected"
    db.session.commit()
    notify(
        card.author_id,
        f'你的角色卡"{card.name}"未通过审核，可修改后重新提交。',
        type_="review",
        related_card_id=card.id,
    )
    db.session.commit()
    flash(f'已拒绝："{card.name}"', "success")
    return redirect(url_for("admin.review"))


# ---------------- 举报处理 ----------------
def _resolve_target_reports(target_type, target_id, handler_id, notice=None):
    """同一被举报对象（角色卡/评论/用户）的所有待处理举报，随本次处理一并标记完成，
    并向举报人发送通知。

    解决“一个对象被多人举报”时，管理员处理一条后其余仍显示待处理，
    且举报人无法得知处理结果的问题。
    """
    pending = Report.query.filter_by(
        target_type=target_type, target_id=str(target_id), status="pending"
    ).all()
    reporter_ids = sorted({r.reporter_id for r in pending})
    if pending:
        Report.query.filter_by(
            target_type=target_type, target_id=str(target_id), status="pending"
        ).update(
            {
                Report.status: "resolved",
                Report.handled_at: db.func.now(),
                Report.handled_by: handler_id,
            },
            synchronize_session=False,
        )
    if notice:
        for rid in reporter_ids:
            notify(rid, notice, type_="report")
    db.session.commit()
    return reporter_ids


@admin_bp.route("/reports")
@super_admin_required
def reports():
    status = request.args.get("status", "pending").strip() or "pending"
    target_type = request.args.get("type", "").strip()
    query = Report.query
    if status != "all":
        query = query.filter(Report.status == status)
    if target_type:
        query = query.filter(Report.target_type == target_type)
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Report.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    stats = dict(
        db.session.query(Report.status, func.count(Report.id))
        .group_by(Report.status)
        .all()
    )
    # 统计同一被举报对象被举报的次数，用于在列表中提示“多人举报”
    rows = (
        db.session.query(Report.target_type, Report.target_id, func.count(Report.id))
        .group_by(Report.target_type, Report.target_id)
        .all()
    )
    counts = {f"{t}:{i}": c for t, i, c in rows}
    return render_template(
        "admin/reports.html",
        reports=pagination.items,
        pagination=pagination,
        args={"status": status, "type": target_type},
        status=status,
        target_type=target_type,
        stats=stats,
        counts=counts,
    )


@admin_bp.route("/reports/<int:report_id>")
@super_admin_required
def report_detail(report_id):
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)

    target_info = {"type": r.target_type, "link": None, "snippet": ""}
    if r.target_type == "card":
        card = db.session.get(Card, r.target_id)
        if card:
            target_info["link"] = url_for("user.card_detail", card_id=card.id)
            target_info["snippet"] = f"名称：{card.name}\n简介：{card.intro[:200]}"
    elif r.target_type == "comment":
        comment = db.session.get(Comment, int(r.target_id))
        if comment:
            target_info["link"] = url_for("user.card_detail", card_id=comment.card_id)
            target_info["snippet"] = comment.content
    elif r.target_type == "user":
        u = db.session.get(User, int(r.target_id))
        if u:
            target_info["link"] = url_for("user.profile", username=u.username)
            target_info["snippet"] = f"用户名：{u.username}\n昵称：{u.nickname}\n邮箱：{u.email}"

    # 统计同一被举报对象被多少人举报，便于管理员判断严重程度
    related = Report.query.filter_by(
        target_type=r.target_type, target_id=r.target_id
    ).all()
    related_total = len(related)
    related_pending = sum(1 for x in related if x.status == "pending")
    related_reporters = [
        (x.reporter.nickname if x.reporter else x.reporter_id) for x in related
    ]

    return render_template(
        "admin/report_detail.html",
        report=r,
        target_info=target_info,
        related_total=related_total,
        related_pending=related_pending,
        related_reporters=related_reporters,
    )


@admin_bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@super_admin_required
def report_resolve(report_id):
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)
    _resolve_target_reports(
        r.target_type,
        r.target_id,
        current_user.id,
        notice="你举报的内容经平台审核已处理完毕，感谢你的反馈。",
    )
    flash(
        "已标记为该举报处理完毕（未采取额外措施），同一对象的其他举报也已一并处理，并已通知举报人",
        "success",
    )
    return redirect(url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/action", methods=["POST"])
@super_admin_required
def report_action(report_id):
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)
    action = (request.form.get("action") or "").strip()

    if action == "hide_card" and r.target_type == "card":
        card = db.session.get(Card, r.target_id)
        if card:
            card.is_hidden = True
    elif action == "delete_card" and r.target_type == "card":
        card = db.session.get(Card, r.target_id)
        if card:
            CardTag.query.filter_by(card_id=card.id).delete()
            CardDialogueStyle.query.filter_by(card_id=card.id).delete()
            CardImage.query.filter_by(card_id=card.id).delete()
            db.session.delete(card)
    elif action == "delete_comment" and r.target_type == "comment":
        comment = db.session.get(Comment, int(r.target_id))
        if comment:
            db.session.delete(comment)
    else:
        flash("无效的处理操作", "warning")
        return redirect(url_for("admin.report_detail", report_id=report_id))

    db.session.commit()
    # 同一对象被多人举报时，本次处理一并解决所有待处理举报，并通知举报人
    notice_map = {
        "hide_card": "你举报的角色卡已被平台下架处理，感谢你的反馈。",
        "delete_card": "你举报的角色卡已被平台删除处理，感谢你的反馈。",
        "delete_comment": "你举报的评论已被平台删除处理，感谢你的反馈。",
    }
    _resolve_target_reports(
        r.target_type, r.target_id, current_user.id, notice=notice_map.get(action)
    )
    flash(
        "已对举报对象采取处理措施，同一对象的其他举报也已一并处理，并已通知举报人",
        "success",
    )
    return redirect(url_for("admin.reports"))
