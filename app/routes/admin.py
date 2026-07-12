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
from ..models import Card, CardDialogueStyle, CardImage, CardTag, Notification, User
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
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(User.username.like(like), User.nickname.like(like), User.email.like(like))
        )
    users = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users, q=q)


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


# ---------------- 角色卡管理 ----------------
@admin_bp.route("/cards")
@super_admin_required
def cards():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    query = Card.query
    if q:
        query = query.filter(Card.name.like(f"%{q}%"))
    if status:
        query = query.filter(Card.status == status)
    cards = query.order_by(Card.created_at.desc()).all()
    return render_template("admin/cards.html", cards=cards, q=q, status=status)


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
    cards = query.order_by(Card.created_at.asc()).all()
    # 统计各状态数量
    stats = dict(
        db.session.query(Card.status, func.count(Card.id)).group_by(Card.status).all()
    )
    return render_template("admin/review_list.html", cards=cards, status=status, stats=stats)


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
