import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/dnaisland"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @staticmethod
    def build_engine_options(db_uri: str = "") -> dict:
        """根据数据库类型生成健壮的连接池与网络超时参数。

        针对本地连接远程云端 MySQL (WAN) 容易发生的：
        - 2013: Lost connection to MySQL server during query
        - 2006: MySQL server has gone away
        特别配置 LIFO 连接池复用、短周期 pool_recycle、主动探测 pool_pre_ping，
        以及针对大包长文本的 max_allowed_packet 和读写超时设置。
        """
        options: dict = {
            "pool_pre_ping": True,
        }
        if db_uri and db_uri.startswith("sqlite"):
            return options

        # MySQL / PostgreSQL 等远程数据库网络连接与连接池参数调优
        options.update(
            {
                "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", 120)),   # 120s 回收，避免被云防火墙/NAT 网关掐断空闲连接
                "pool_size": int(os.environ.get("DB_POOL_SIZE", 10)),          # 连接池基准连接数
                "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 20)),     # 允许突发连接数
                "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", 30)),     # 排队等待连接超时
                "pool_use_lifo": True,                                          # 优先复用最新活跃连接（LIFO），大幅减少空闲断连
                "connect_args": {
                    "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", 15)), # TCP 建连超时 (秒)
                    "read_timeout": int(os.environ.get("DB_READ_TIMEOUT", 60)),       # 读超时 (秒)
                    "write_timeout": int(os.environ.get("DB_WRITE_TIMEOUT", 60)),     # 写超时 (秒)
                    "charset": "utf8mb4",
                    "max_allowed_packet": 64 * 1024 * 1024,                           # 64MB 允许大卡片/图片传输
                },
            }
        )
        return options

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 120,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
        "pool_use_lifo": True,
        "connect_args": {
            "connect_timeout": 15,
            "read_timeout": 60,
            "write_timeout": 60,
            "charset": "utf8mb4",
            "max_allowed_packet": 64 * 1024 * 1024,
        },
    }

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.example.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@dnaisland.local")

    # 搜索是否启用 MySQL 全文索引（FULLTEXT + ngram）加速。
    # 生产 MySQL 跑过对应迁移后开启（默认开启）；SQLite 等不支持 FULLTEXT 的引擎
    # 由应用层自动回退到 LIKE，本开关在非 MySQL 下不生效。
    FULLTEXT_SEARCH = os.environ.get("SEARCH_FULLTEXT", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    # 请求体大小上限：避免带图发帖等场景的 base64 表单字段过大触发 413。
    # Werkzeug 默认 MAX_FORM_MEMORY_SIZE 仅 500KB，超大 data URL 会直接 413，
    # 故在此显式放宽；同时客户端会对配图压缩到有界 WebP，进一步降低负载。
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024       # 整体请求体上限 16MB
    MAX_FORM_MEMORY_SIZE = 16 * 1024 * 1024     # 单个表单字段在内存中的上限 16MB


config = Config()
