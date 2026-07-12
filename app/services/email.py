from flask_mail import Message

from ..extensions import mail


def send_verification_email(to: str, code: str) -> None:
    """发送注册邮箱验证码邮件。"""
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
    mail.send(msg)
