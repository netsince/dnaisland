import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/dnaisland"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.example.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@dnaisland.local")

    # 请求体大小上限：避免带图发帖等场景的 base64 表单字段过大触发 413。
    # Werkzeug 默认 MAX_FORM_MEMORY_SIZE 仅 500KB，超大 data URL 会直接 413，
    # 故在此显式放宽；同时客户端会对配图压缩到有界 WebP，进一步降低负载。
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024       # 整体请求体上限 16MB
    MAX_FORM_MEMORY_SIZE = 16 * 1024 * 1024     # 单个表单字段在内存中的上限 16MB


config = Config()
