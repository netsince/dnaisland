"""转发 API（BYOK 代理）相关模型。

- ProxyConfig: 用户级上游配置（一个账号一条：user_id 唯一）。
- ProxyLog:    每次转发一条审计日志（请求体 + 响应体成对落库）。
"""

import uuid

from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db

# 平台签发的代理访问令牌前缀。带前缀便于识别「平台 key」与用户填写的真实上游 key。
TOKEN_PREFIX = "sk-dnaisland-"
# 平台签发给用户使用的「对外 base 路径」（站点域名 + 该路径即为对外 base URL）
PUBLIC_BASE_PATH = "/proxyapi/v1"


def generate_token() -> str:
    """生成新的平台签发访问令牌：sk-dnaisland-{uuid}。"""
    return f"{TOKEN_PREFIX}{uuid.uuid4()}"


class ProxyConfig(db.Model):
    """一个账号一条的上游转发配置。"""

    __tablename__ = "proxy_configs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    upstream_base_url = db.Column(db.Text, nullable=False)  # 上游 OpenAI 兼容地址
    upstream_api_key = db.Column(db.Text, nullable=False)  # 上游密钥（加密存储）
    token = db.Column(db.String(120), nullable=False, unique=True, index=True)  # sk-dnaisland-{uuid}
    remark = db.Column(db.String(120), nullable=True)  # 备注/名称，可空
    enabled = db.Column(
        db.Boolean, nullable=False, server_default="1", default=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )

    user = db.relationship("User", backref="proxy_config")

    def __repr__(self):
        return f"<ProxyConfig user={self.user_id} token={self.token}>"


class ProxyLog(db.Model):
    """一次转发请求的审计日志（请求体 + 响应体成对保存）。"""

    __tablename__ = "proxy_logs"

    id = db.Column(db.Integer, primary_key=True)
    # 可空：鉴权失败的请求没有对应用户，同样需要审计
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )
    config_id = db.Column(
        db.Integer, db.ForeignKey("proxy_configs.id"), nullable=True, index=True
    )
    token = db.Column(db.String(120), nullable=True)  # 快照：本次使用的平台令牌
    method = db.Column(db.String(16), nullable=False)
    path = db.Column(db.String(500), nullable=True)  # 请求路径（不含域名）
    upstream_url = db.Column(db.Text, nullable=True)  # 实际转发到的上游 URL
    request_body = db.deferred(db.Column(LONGTEXT, nullable=True))  # 请求体原文
    status_code = db.Column(db.Integer, nullable=True)  # 上游返回状态码
    response_body = db.deferred(db.Column(LONGTEXT, nullable=True))  # 响应体原文
    duration_ms = db.Column(db.Integer, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), index=True
    )

    user = db.relationship("User", backref="proxy_logs")
    config = db.relationship("ProxyConfig", backref="logs")
