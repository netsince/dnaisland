from email_validator import EmailNotValidError, validate_email
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, login_manager
from ..models import User
from ..services.email import (
    send_password_changed_email,
    send_password_reset_email,
    send_verification_email,
)
from ..services.login_service import (
    MSG_BAD_PASSWORD,
    MSG_THROTTLED,
    MSG_USER_NOT_FOUND,
    find_user_by_identifier,
    login_throttled,
    record_login_failure,
)
from ..services.site_service import check_email_allowed
from ..services.verification_code_service import (
    can_resend,
    create_code,
    verify_code,
)
from ..utils import get_user_by_username, rate_hit, respond

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


@auth_bp.before_app_request
def _enforce_session_epoch():
    """会话代数校验：密码改过之后，旧 Cookie 一律作废（含「记住我」）。

    为什么不写在 load_user 里：那里只拿到 user_id，而且必须返回一个用户对象；在这里
    登出才能连「记住我」Cookie 一起清掉（logout_user 会一并处理），用户下一跳即登录页。

    **缺失按 0 处理**：新增这一列之前发出的 Cookie 里没有这个键，若按"必须相等"硬判，
    部署那一刻会把所有已登录用户踢下线。库里默认也是 0，于是部署后无人受影响，
    只有真的改过密码（epoch ≥ 1）的用户才会被登出。
    """
    if request.endpoint == "static":
        return  # 静态资源不做会话校验（否则每个资源请求都要查一次库）
    if not current_user.is_authenticated:
        return
    if session.get("session_epoch", 0) == (current_user.session_epoch or 0):
        return
    logout_user()
    flash("登录状态已失效，请重新登录。", "warning")


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

    allowed, suffixes = check_email_allowed(email)
    if not allowed:
        return jsonify(
            ok=False,
            message="该邮箱不在注册白名单内，支持的后缀：" + "、".join(suffixes),
        ), 403

    code = create_code(email)
    try:
        send_verification_email(email, code)
    except Exception as exc:  # noqa: BLE001 - 邮件失败需明确反馈给用户
        current_app.logger.exception("发送验证码邮件失败: %s", exc)
        return jsonify(ok=False, message="邮件发送失败，请稍后重试"), 500

    return jsonify(ok=True, message="验证码已发送，请查收邮箱")


# ---------------------------------------------------------------------------
# 找回密码（仅网页版；App 端不提供）
#
# 流程与注册一致：填邮箱 → 点「发送验证码」→ 填验证码 + 新密码 → 提交。
# 三个安全要点（都有用例守着）：
#  1. **不泄露账号是否存在**：无论邮箱有没有注册、是否被封禁、是否在重发冷却内，
#     /auth/reset-code 的响应完全相同；不该发信时就是"静默不发"。
#  2. **验证码用途分离**：用 purpose="reset" 建码，注册码不能重置密码，反之亦然。
#  3. **提交接口按 IP 限流**：6 位验证码 10 分钟有效，不限制尝试次数就能暴力猜。
# ---------------------------------------------------------------------------
RESET_SEND_SCOPE = "password_reset_send"
RESET_SEND_LIMIT = 5  # 同一 IP 每 10 分钟最多请求发送 5 次
RESET_SEND_WINDOW = 600
RESET_SUBMIT_SCOPE = "password_reset_submit"
RESET_SUBMIT_LIMIT = 10  # 同一 IP 每 10 分钟最多提交 10 次
RESET_SUBMIT_WINDOW = 600

# 与「是否注册/是否封禁/是否冷却中」无关的同一句话，避免账号枚举。
RESET_SENT_MESSAGE = "如果该邮箱已注册，我们已发送验证码，请查收邮箱（含垃圾箱）。"


@auth_bp.route("/forgot", methods=["GET"])
def forgot():
    """找回密码页面（表单提交到 reset_password）。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    return render_template("auth/forgot.html")


@auth_bp.route("/reset-code", methods=["POST"])
def reset_code():
    """发送找回密码验证码。响应恒定，不暴露邮箱是否注册。"""
    payload = request.get_json(silent=True) or {}
    raw_email = (payload.get("email") or "").strip()

    # 限流命中时同样返回恒定文案（不告诉调用方"这邮箱存在/刚发过"）。
    if rate_hit(RESET_SEND_SCOPE, limit=RESET_SEND_LIMIT, per=RESET_SEND_WINDOW):
        return jsonify(ok=True, message=RESET_SENT_MESSAGE)

    try:
        valid = validate_email(raw_email, check_deliverability=False)
        email = valid.normalized
    except EmailNotValidError:
        # 邮箱格式本身不是秘密，可以直接提示（否则用户填错了也毫无反馈）。
        return jsonify(ok=False, message="请输入有效的邮箱地址"), 400

    user = User.query.filter_by(email=email).first()
    # 未注册 / 已注销 / 被封禁：都不发信（封禁用户不该靠找回密码恢复访问），
    # 但对外仍然是同一句成功文案。
    if user is not None and not user.is_locked and can_resend(email, purpose="reset"):
        code = create_code(email, purpose="reset")
        try:
            send_password_reset_email(email, code)
        except Exception as exc:  # noqa: BLE001 - 邮件失败只记日志，不改变对外响应
            current_app.logger.exception("发送找回密码邮件失败: %s", exc)

    return jsonify(ok=True, message=RESET_SENT_MESSAGE)


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    """校验验证码并设置新密码。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    email = (request.form.get("email") or "").strip()
    code = (request.form.get("code") or "").strip()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""

    def back(message: str, category: str = "danger"):
        flash(message, category)
        return render_template("auth/forgot.html", email=email, code=code), 200

    if rate_hit(RESET_SUBMIT_SCOPE, limit=RESET_SUBMIT_LIMIT, per=RESET_SUBMIT_WINDOW):
        return back("尝试过于频繁，请稍后再试")

    if not (email and code and password):
        return back("请填写邮箱、验证码和新密码")
    if password != confirm:
        return back("两次输入的新密码不一致")
    if len(password) < 6:
        return back("新密码至少 6 位")

    try:
        valid = validate_email(email, check_deliverability=False)
        email = valid.normalized
    except EmailNotValidError:
        return back("邮箱格式不正确")

    user = User.query.filter_by(email=email).first()
    if user is None or user.is_locked:
        # 与"验证码错误"同一句话：不暴露账号是否存在/是否被封禁。
        return back("验证码无效或已过期，请重新获取")

    if not verify_code(email, code, purpose="reset"):
        return back("验证码无效或已过期，请重新获取")

    user.set_password(password)
    db.session.commit()

    try:
        send_password_changed_email(email)
    except Exception as exc:  # noqa: BLE001 - 通知失败不影响改密结果
        current_app.logger.exception("发送密码变更通知失败: %s", exc)

    flash("密码已重置，请用新密码登录。", "success")
    return redirect(url_for("auth.login"))


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

        allowed, suffixes = check_email_allowed(email)
        if not allowed:
            flash(
                "该邮箱不在注册白名单内，支持的邮箱后缀：" + "、".join(suffixes),
                "danger",
            )
            return render_template("auth/register.html")

        if get_user_by_username(username):
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

        login_user(user, remember=True)
        session["session_epoch"] = user.session_epoch or 0
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

        if login_throttled():
            flash(MSG_THROTTLED, "danger")
            return render_template("auth/login.html")

        user = find_user_by_identifier(identifier)

        if user is not None and user.is_locked:
            if user.is_deleted:
                flash("该账号已被管理员删除，无法登录。", "danger")
            elif user.is_cancelled:
                flash("该账号已注销，无法登录。", "danger")
            else:
                flash("该账号已被封禁，无法登录。", "danger")
            return render_template("auth/login.html")

        # 区分「账号不存在」与「密码错误」（产品要求）：这会让账号可被枚举，
        # 因此上方 login_throttled() 的失败限流是必需的配套措施，不可单独移除。
        if user is None:
            record_login_failure()
            flash(MSG_USER_NOT_FOUND, "danger")
            return render_template("auth/login.html")

        if not user.check_password(password):
            record_login_failure()
            flash(MSG_BAD_PASSWORD, "danger")
            return render_template("auth/login.html")

        remember = bool(request.form.get("remember"))
        login_user(user, remember=remember)
        # 记录本次会话对应的密码代数：日后改密码会让它不匹配，从而登出所有旧设备。
        session["session_epoch"] = user.session_epoch or 0
        flash(f"欢迎回来，{user.nickname}！", "success")
        return redirect(next_url)

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return respond(url_for("main.index"), flash_msg="已退出登录", flash_cat="info")
