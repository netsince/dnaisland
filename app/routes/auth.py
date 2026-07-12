import datetime

from email_validator import EmailNotValidError, validate_email
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from ..extensions import db, login_manager
from ..models import User, VerificationCode
from ..services.email import send_verification_email
from ..services.verification_code_service import (
    can_resend,
    create_code,
    verify_code,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


@auth_bp.route("/send-code", methods=["POST"])
def send_code():
    payload = request.get_json(silent=True) or {}
    raw_email = (payload.get("email") or "").strip()
    try:
        valid = validate_email(raw_email, check_deliverability=False)
        email = valid.normalized
    except EmailNotValidError:
        return jsonify(ok=False, message="请输入有效的邮箱地址"), 400

    if not can_resend(email):
        return jsonify(ok=False, message="验证码发送过于频繁，请稍后再试"), 429

    code = create_code(email)
    try:
        send_verification_email(email, code)
    except Exception as exc:  # noqa: BLE001 - 邮件失败需明确反馈给用户
        current_app.logger.exception("发送验证码邮件失败: %s", exc)
        return jsonify(ok=False, message="邮件发送失败，请稍后重试"), 500

    return jsonify(ok=True, message="验证码已发送，请查收邮箱")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        nickname = (request.form.get("nickname") or "").strip()
        raw_email = (request.form.get("email") or "").strip()
        code = (request.form.get("code") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not (username and nickname and raw_email and code and password):
            flash("请填写所有必填项", "danger")
            return render_template("auth/register.html")

        if password != confirm:
            flash("两次输入的密码不一致", "danger")
            return render_template("auth/register.html")

        try:
            valid = validate_email(raw_email, check_deliverability=False)
            email = valid.normalized
        except EmailNotValidError:
            flash("邮箱格式不正确", "danger")
            return render_template("auth/register.html")

        if User.query.filter_by(username=username).first():
            flash("该用户名已被注册", "danger")
            return render_template("auth/register.html")
        if User.query.filter_by(email=email).first():
            flash("该邮箱已被注册", "danger")
            return render_template("auth/register.html")

        if not verify_code(email, code):
            flash("邮箱验证码无效或已过期", "danger")
            return render_template("auth/register.html")

        user = User(
            username=username,
            nickname=nickname,
            email=email,
            email_verified=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("注册成功，欢迎来到 DNAISLAND！", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or url_for("main.index")

        user = User.query.filter(
            or_(User.username == identifier, User.email == identifier)
        ).first()

        if user is None or not user.check_password(password):
            flash("用户名/邮箱或密码错误", "danger")
            return render_template("auth/login.html")

        login_user(user)
        flash(f"欢迎回来，{user.nickname}！", "success")
        return redirect(next_url)

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已退出登录", "info")
    return redirect(url_for("main.index"))
