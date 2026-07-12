import os

from dotenv import load_dotenv
from flask import Flask
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

    from .routes import admin_bp, auth_bp, main_bp, publish_bp, user_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(publish_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)

    from .commands import init_commands

    init_commands(app)

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
