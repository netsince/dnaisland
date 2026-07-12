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
    Report,
    User,
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
        if role not in ("user", "super_admin", "banned"):
            role = "user"
        u = User(username=username, nickname=nickname, email=email, role=role, status=status)
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
        if u.role not in ("user", "super_admin", "banned"):
            u.role = "user"
        u.status = request.form.get("status") or u.status
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


@admin_bp.route("/users/<int:user_id>/ban", methods=["POST"])
@super_admin_required
def user_ban(user_id):
    u = db.session.get(User, user_id)
    if not u:
        abort(404)
    if u.id == current_user.id:
        flash("不能封禁当前登录的账号", "danger")
        return redirect(url_for("admin.users"))
    u.role = "banned"
    db.session.commit()
    flash(f'已封禁用户"{u.username}"', "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/unban", methods=["POST"])
@super_admin_required
def user_unban(user_id):
    u = db.session.get(User, user_id)
    if not u:
        abort(404)
    u.role = "user"
    db.session.commit()
    flash(f'已解除封禁用户"{u.username}"', "success")
    return redirect(url_for("admin.users"))


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
    return render_template(
        "admin/reports.html",
        reports=pagination.items,
        pagination=pagination,
        args={"status": status, "type": target_type},
        status=status,
        target_type=target_type,
        stats=stats,
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

    return render_template(
        "admin/report_detail.html", report=r, target_info=target_info
    )


@admin_bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@super_admin_required
def report_resolve(report_id):
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)
    r.status = "resolved"
    r.handled_at = db.func.now()
    r.handled_by = current_user.id
    db.session.commit()
    flash("已标记为该举报处理完毕（未采取额外措施）", "success")
    return redirect(url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/action", methods=["POST"])
@super_admin_required
def report_action(report_id):
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)
    action = (request.form.get("action") or "").strip()

    if action == "ban_user" and r.target_type == "user":
        u = db.session.get(User, int(r.target_id))
        if u and u.id != current_user.id:
            u.role = "banned"
    elif action == "hide_card" and r.target_type == "card":
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

    r.status = "resolved"
    r.handled_at = db.func.now()
    r.handled_by = current_user.id
    db.session.commit()
    flash("已对举报对象采取处理措施", "success")
    return redirect(url_for("admin.reports"))
