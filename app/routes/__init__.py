from .admin import admin_bp
from .auth import auth_bp
from .main import main_bp
from .publish import publish_bp
from .user import user_bp
from .teahouse import teahouse_bp

__all__ = ["auth_bp", "main_bp", "publish_bp", "admin_bp", "user_bp", "teahouse_bp"]
