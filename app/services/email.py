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
        f"<p>你的邮箱验证码是：<b style=\"font-size:20px;letter-spacing:2px\">{code}</b></p>"
        f"<p>该验证码 10 分钟内有效，请勿泄露给他人。</p>"
    )
    # 绑定请求期间的 app 对象，后台线程内重建上下文以访问 Flask-Mail 配置
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    threading.Thread(target=_send_mail, args=(app, msg), daemon=True).start()


def _send_mail(app, msg) -> None:
    """在独立线程内发送邮件；失败仅记日志，不向上抛。"""
    with app.app_context():
        try:
            mail.send(msg)
        except Exception:
            app.logger.exception("邮件发送失败: %s", msg.recipients)
