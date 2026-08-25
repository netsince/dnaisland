"""转发 API（BYOK 代理）用户配置与密钥加密。

- 上游 API key 使用 Fernet 可逆加密落库（绝不存明文）；
- 加密密钥默认由 SECRET_KEY 派生（SHA-256），可用环境变量 PROXY_ENC_KEY 覆盖；
- 一个账号一条 ProxyConfig（user_id 唯一）。
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from ..extensions import db
from ..models import ProxyConfig

_fernet_cache: dict[str, Fernet] = {}


def _fernet() -> Fernet:
    """构造（并缓存）Fernet 实例：优先 PROXY_ENC_KEY，否则由 SECRET_KEY 派生。"""
    cached = _fernet_cache.get("f")
    if cached is not None:
        return cached
    env_key = (os.environ.get("PROXY_ENC_KEY") or "").strip()
    if env_key:
        key = env_key.encode("ascii")
    else:
        secret = current_app.config.get("SECRET_KEY") or "dnaisland-fallback"
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    f = Fernet(key)
    _fernet_cache["f"] = f
    return f


def encrypt_secret(plain: str) -> str:
    """加密上游 API key；空串原样返回。"""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(cipher: str) -> str:
    """解密上游 API key；失败（密钥轮换/数据损坏）返回空串。"""
    if not cipher:
        return ""
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def get_user_config(user_id):
    """取某用户的转发配置（无则 None）。"""
    return db.session.query(ProxyConfig).filter_by(user_id=user_id).first()


def upsert_config(
    user_id,
    *,
    upstream_base_url,
    upstream_api_key_plain=None,
    remark=None,
    enabled=True,
):
    """创建或更新用户的转发配置。返回 (config, error)。

    - upstream_api_key_plain 为空表示「保持现有密钥不变」（仅编辑时合法），新建时必须提供；
    - token 只在首次创建时生成，编辑不换 key（换 key 走 reset_token）。
    """
    base_url = (upstream_base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return None, "上游 Base URL 必须以 http:// 或 https:// 开头"

    cfg = get_user_config(user_id)
    plain = (upstream_api_key_plain or "").strip()
    if cfg is None:
        if not plain:
            return None, "请填写上游 API Key"
        from ..models.proxy import generate_token

        cfg = ProxyConfig(
            user_id=user_id,
            upstream_base_url=base_url,
            upstream_api_key=encrypt_secret(plain),
            token=generate_token(),
            enabled=bool(enabled),
            remark=(remark or "").strip() or None,
        )
        db.session.add(cfg)
    else:
        if plain:
            cfg.upstream_api_key = encrypt_secret(plain)
        cfg.upstream_base_url = base_url
        cfg.enabled = bool(enabled)
        cfg.remark = (remark or "").strip() or None
    db.session.commit()
    return cfg, None


def reset_token(user_id):
    """重置平台签发的访问令牌（旧的立即失效）。返回新令牌；无配置返回 None。"""
    cfg = get_user_config(user_id)
    if cfg is None:
        return None
    from ..models.proxy import generate_token

    cfg.token = generate_token()
    db.session.commit()
    return cfg.token


def delete_config(user_id) -> bool:
    """删除用户的转发配置（历史日志保留，config_id 置空）。返回是否删除。"""
    cfg = get_user_config(user_id)
    if cfg is None:
        return False
    from ..models import ProxyLog

    ProxyLog.query.filter_by(config_id=cfg.id).update({"config_id": None})
    db.session.delete(cfg)
    db.session.commit()
    return True
