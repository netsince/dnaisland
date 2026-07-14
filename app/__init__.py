import os

from dotenv import load_dotenv
from flask import Flask, url_for
from flask_login import current_user

from .config import config
from .extensions import bcrypt, db, login_manager, mail, migrate

load_dotenv()


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or config)

    # 环境变量可覆盖关键配置
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", app.config["SECRET_KEY"])
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", app.config["SQLALCHEMY_DATABASE_URI"]
    )
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", app.config["MAIL_SERVER"])
    app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", app.config["MAIL_PORT"]))
    app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", app.config["MAIL_USERNAME"])
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", app.config["MAIL_PASSWORD"])
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get(
        "MAIL_DEFAULT_SENDER", app.config["MAIL_DEFAULT_SENDER"]
    )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app)

    # 将 current_user 注册为 Jinja 全局变量，确保被 import 的 macro（默认 without
    # context）也能访问到，避免 'current_user' is undefined 报错
    app.jinja_env.globals["current_user"] = current_user

    # 判断某条茶馆帖对当前用户是否可见：未隐藏可见；隐藏帖仅作者/超级管理员可见
    def _teapost_visible(post):
        if not post.is_hidden:
            return True
        u = current_user
        return bool(u.is_authenticated and (u.id == post.user_id or u.is_super_admin))

    app.jinja_env.globals["teapost_visible"] = _teapost_visible

    from .routes import (
        admin_bp,
        auth_bp,
        main_bp,
        publish_bp,
        user_bp,
        teahouse_bp,
    )

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(publish_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(teahouse_bp)

    from .commands import init_commands

    init_commands(app)

    import re as _re

    from markupsafe import Markup, escape

    _URL_RE = _re.compile(r"(https?://[^\s<]+)")

    @app.template_filter("linkify")
    def linkify(text):
        """把纯文本里的 URL 转成可点击链接（先转义防 XSS，再替换 URL）。"""
        if not text:
            return ""
        escaped = str(escape(text))
        linked = _URL_RE.sub(
            r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
            escaped,
        )
        return Markup(linked)

    from urllib.parse import urlparse

    @app.template_filter("teahouse_linkify")
    def teahouse_linkify(text):
        """茶馆帖子专用：内链（本站域名或 / 开头）直接可点；外链经 /leave 确认页。

        先整体转义防 XSS，再替换 URL。
        """
        from flask import request

        if not text:
            return ""
        escaped = str(escape(text))
        req_host = request.host.lower() if request else ""

        def _repl(m):
            url = m.group(1)
            host = urlparse(url).netloc.lower()
            # 本站链接：直接可点
            if (host and host == req_host) or url.startswith("/"):
                return (
                    f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
                )
            # 外链：经中转确认页
            leave = url_for("teahouse.leave", url=url)
            return (
                f'<a href="{leave}" target="_blank" rel="noopener noreferrer">{url}</a>'
            )

        return Markup(_URL_RE.sub(_repl, escaped))

    @app.context_processor
    def inject_unread():
        from .services.notification_service import unread_count

        if current_user.is_authenticated:
            try:
                return {"unread_notifications": unread_count(current_user.id)}
            except Exception:
                return {"unread_notifications": 0}
        return {"unread_notifications": 0}

    return app
