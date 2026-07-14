import json

from ..extensions import db


class SiteConfig(db.Model):
    """站点级配置（单例行，id 固定为 1）。

    涵盖：关站状态、公告（富文本/HTML）、首页 Hero、协议链接、注册邮箱白名单。
    """

    __tablename__ = "site_config"

    id = db.Column(db.Integer, primary_key=True, default=1)  # 单例行
    site_name = db.Column(db.String(120), server_default="DNAISLAND", nullable=False)

    # —— 关站 ——
    shutdown_enabled = db.Column(db.Boolean, server_default="0", nullable=False)
    shutdown_message = db.Column(db.Text, nullable=True)

    # —— 公告（富文本 / HTML）——
    announcement_enabled = db.Column(db.Boolean, server_default="0", nullable=False)
    announcement_content = db.Column(db.Text, nullable=True)

    # —— 首页 Hero ——
    hero_enabled = db.Column(db.Boolean, server_default="1", nullable=False)
    hero_title = db.Column(db.Text, nullable=True)
    hero_subtitle = db.Column(db.Text, nullable=True)
    # JSON 数组：[{ "label": "...", "url": "..." }, ...]
    hero_buttons = db.Column(db.Text, nullable=True)

    # —— 协议链接（外部 URL）——
    privacy_policy_url = db.Column(db.String(500), nullable=True)
    tos_url = db.Column(db.String(500), nullable=True)

    # —— 注册邮箱白名单 ——
    email_whitelist_enabled = db.Column(db.Boolean, server_default="0", nullable=False)
    # 逗号 / 换行 / 空格分隔的后缀，如 @gmail.com、@qq.com
    email_whitelist_suffixes = db.Column(db.Text, nullable=True)

    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )

    def hero_buttons_list(self):
        if not self.hero_buttons:
            return []
        try:
            data = json.loads(self.hero_buttons)
            if isinstance(data, list):
                return [
                    {"label": str(b.get("label", "")), "url": str(b.get("url", ""))}
                    for b in data
                    if isinstance(b, dict) and b.get("label")
                ]
        except (ValueError, TypeError):
            return []
        return []

    def email_suffixes_list(self):
        raw = (self.email_whitelist_suffixes or "").strip()
        if not raw:
            return []
        out = []
        for part in raw.replace("\n", ",").split(","):
            s = part.strip().lower()
            if not s:
                continue
            if not s.startswith("@"):
                s = "@" + s
            out.append(s)
        # 去重保序
        seen = set()
        result = []
        for s in out:
            if s not in seen:
                seen.add(s)
                result.append(s)
        return result


class Article(db.Model):
    """站点文章（仅管理员后台发布）。"""

    __tablename__ = "articles"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=False)  # 富文本 / HTML
    cover = db.Column(db.Text, nullable=True)  # 封面图 URL / base64

    author_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    is_published = db.Column(db.Boolean, server_default="1", nullable=False, index=True)
    # 发布者是否公开：False 时隐藏发布者身份（匿名管理员）
    show_author = db.Column(db.Boolean, server_default="1", nullable=False)

    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )

    author = db.relationship("User", backref="articles")
