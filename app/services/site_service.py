from ..extensions import db
from ..models import SiteConfig


def get_site_config():
    """取站点配置单例行；不存在则创建默认行。"""
    cfg = db.session.get(SiteConfig, 1)
    if cfg is None:
        cfg = SiteConfig(id=1)
        db.session.add(cfg)
        db.session.commit()
    return cfg


def default_public_config():
    """数据库不可用时回退的默认公开配置（结构须与正常返回一致）。"""
    return {
        "site_name": "DNAISLAND",
        "shutdown": {"enabled": False, "message": ""},
        "announcement": {"enabled": False, "content": ""},
        "hero": {"enabled": True, "title": "", "subtitle": "", "buttons": []},
        "agreements": {"privacy_policy_url": "", "tos_url": ""},
        "email_whitelist": {"enabled": False, "suffixes": []},
    }


def public_config():
    """对外公开配置（用于模板上下文与公开 JSON API）。

    任何数据库异常都回退到默认配置，避免模板因缺少键而 500。
    """
    try:
        cfg = get_site_config()
    except Exception:
        return default_public_config()
    return {
        "site_name": cfg.site_name,
        "shutdown": {
            "enabled": bool(cfg.shutdown_enabled),
            "message": cfg.shutdown_message or "",
        },
        "announcement": {
            "enabled": bool(cfg.announcement_enabled),
            "content": cfg.announcement_content or "",
        },
        "hero": {
            "enabled": bool(cfg.hero_enabled),
            "title": cfg.hero_title or "",
            "subtitle": cfg.hero_subtitle or "",
            "buttons": cfg.hero_buttons_list(),
        },
        "agreements": {
            "privacy_policy_url": cfg.privacy_policy_url or "",
            "tos_url": cfg.tos_url or "",
        },
        "email_whitelist": {
            "enabled": bool(cfg.email_whitelist_enabled),
            "suffixes": cfg.email_suffixes_list(),
        },
    }


def check_email_allowed(email):
    """检查邮箱是否通过白名单。

    返回 (allowed: bool, suffixes: list)。未启用白名单或无后缀时一律放行。
    """
    cfg = get_site_config()
    if not cfg.email_whitelist_enabled:
        return True, []
    suffixes = cfg.email_suffixes_list()
    if not suffixes:
        return True, []
    low = (email or "").lower()
    allowed = any(low.endswith(s) for s in suffixes)
    return allowed, suffixes
