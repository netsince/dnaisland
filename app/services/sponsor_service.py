"""赞助者判定（网页版模板与 JSON API 共用）。

赞助者在昵称旁显示红星装饰。网页版通过 Jinja 全局 `is_sponsor` 调用，
JSON API 通过 `_user_public()` 的 `is_sponsor` 字段输出 —— 两边必须是
同一口径，所以判定逻辑收敛到这里，谁都不再自己查表。

性能：赞助者集合**每请求只查一次**，缓存在 `flask.g`。列表页（卡片列表/
评论/茶馆 Feed/关注列表）每一行都要判定，逐行查库就是 N+1。
"""

from flask import g

from ..extensions import db


def sponsor_user_ids() -> set[int]:
    """当前赞助者的 user_id 集合（每请求缓存一次）。"""
    cached = getattr(g, "_sponsor_user_ids", None)
    if cached is not None:
        return cached

    # 延迟导入：models 里 Sponsor 与 User 互有外键关系，模块级导入易成环。
    from ..models import Sponsor

    ids = {sid for (sid,) in db.session.query(Sponsor.user_id).all()}
    g._sponsor_user_ids = ids
    return ids


def is_sponsor(user_id) -> bool:
    """该用户是否为赞助者。只能在应用/请求上下文里调用（用到 flask.g）。"""
    if not user_id:
        return False
    return user_id in sponsor_user_ids()


def is_sponsor_safe(user_id) -> bool:
    """脱离请求上下文也能安全调用（脚本、后台任务里序列化用户时）。

    `flask.g` 只在应用/请求上下文里可用。这里把"没有上下文/查库失败"降级为
    非赞助者 —— 一个装饰性标记不该让业务流程报错。
    """
    try:
        return is_sponsor(user_id)
    except Exception:
        return False
