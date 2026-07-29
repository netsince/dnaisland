from .admin import admin_bp
from .api import api_bp
from .auth import auth_bp
from .image_gen import image_gen_bp
from .main import main_bp
from .points import points_bp
from .publish import publish_bp
from .sticker import sticker_bp
from .system import system_bp
from .teahouse import teahouse_bp
from .user import user_bp

__all__ = [
    "admin_bp",
    "api_bp",
    "auth_bp",
    "main_bp",
    "publish_bp",
    "system_bp",
    "user_bp",
    "teahouse_bp",
    "points_bp",
    "image_gen_bp",
    "sticker_bp",
]
