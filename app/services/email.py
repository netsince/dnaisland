"""邮件发送工具。"""

import threading

from flask import current_app
from flask_mail import Message

from ..extensions import mail


def send_verification_email(to: str, code: str) -> None:
    """发送注册邮箱验证码邮件（后台线程异步发送，不阻塞请求）。

    验证码已由调用方先落库，邮件异步发送失败仅记录日志、不影响主流程；
    用户若未收到，可在频控通过后重新请求发送。
    """
    msg = Message(subject="DNAISLAND 邮箱验证码", recipients=[to])
    msg.body = (
        f"欢迎注册 DNAISLAND！\n\n"
        f"你的邮箱验证码是：{code}\n"
        f"该验证码 10 分钟内有效，请勿泄露给他人。"
    )
    msg.html = (
        f"<p>欢迎注册 <b>DNAISLAND</b>！</p>"
        f'<p>你的邮箱验证码是：<b style="font-size:20px;letter-spacing:2px">{code}</b></p>'
        f"<p>该验证码 10 分钟内有效，请勿泄露给他人。</p>"
    )
    _send_async(msg)


def send_password_reset_email(to: str, code: str) -> None:
    """发送找回密码验证码邮件（异步、失败仅记日志，与注册验证码同一套）。

    与注册验证码**用途分离**（数据库里 purpose 不同）：注册码不能用来重置密码，
    反之亦然。文案里明确写出「不是你本人操作请忽略」，避免用户误以为账号被盗。
    """
    msg = Message(subject="DNAISLAND 找回密码验证码", recipients=[to])
    msg.body = (
        f"我们收到了重置 DNAISLAND 账号密码的请求。\n\n"
        f"你的验证码是：{code}\n"
        f"该验证码 10 分钟内有效，请勿泄露给他人。\n\n"
        f"如果这不是你本人的操作，请忽略本邮件，你的密码不会改变。"
    )
    msg.html = (
        f"<p>我们收到了重置 <b>DNAISLAND</b> 账号密码的请求。</p>"
        f'<p>你的验证码是：<b style="font-size:20px;letter-spacing:2px">{code}</b></p>'
        f"<p>该验证码 10 分钟内有效，请勿泄露给他人。</p>"
        f'<p style="color:#888">如果这不是你本人的操作，请忽略本邮件，你的密码不会改变。</p>'
    )
    _send_async(msg)


def send_password_changed_email(to: str) -> None:
    """密码已被重置后给账号主人发一封通知（安全惯例：不是本人操作时能立刻发现）。"""
    msg = Message(subject="DNAISLAND 密码已修改", recipients=[to])
    msg.body = (
        "你的 DNAISLAND 账号密码刚刚通过「找回密码」被重置。\n\n"
        "如果这不是你本人的操作，请立即用新密码登录并再次修改，或联系站长。"
    )
    msg.html = (
        "<p>你的 <b>DNAISLAND</b> 账号密码刚刚通过「找回密码」被重置。</p>"
        '<p style="color:#c00">如果这不是你本人的操作，请立即用新密码登录并再次修改，或联系站长。</p>'
    )
    _send_async(msg)


def _send_async(msg: Message) -> None:
    """绑定请求期间的 app 对象，后台线程异步发送（不阻塞请求）。"""
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    threading.Thread(target=_send_mail, args=(app, msg), daemon=True).start()


def _send_mail(app, msg) -> None:
    """在独立线程内发送邮件；失败仅记日志，不向上抛。"""
    with app.app_context():
        try:
            mail.send(msg)
        except Exception:
            app.logger.exception("邮件发送失败: %s", msg.recipients)
