"""跨路由复用的小工具函数。"""

import time

from flask import abort, flash, g, jsonify, redirect, request
from flask_login import current_user

from .extensions import db


def get_user_by_username(username):
    """按用户名查询用户，统一替代各处内联的 User.query.filter_by(username=...)。"""
    from .models.user import User

    if not username:
        return None
    return User.query.filter_by(username=username).first()


def status_counts(model_cls, base_query=None):
    """统计某模型各 status 的数量，返回 {status: count}。

    base_query 可传入已带过滤条件（如按作者）的查询；status 分组不会被过滤掉，
    从而始终能看到全部状态的计数。
    """
    from sqlalchemy import func

    q = base_query if base_query is not None else model_cls.query
    return dict(
        q.with_entities(model_cls.status, func.count(model_cls.id))
        .group_by(model_cls.status)
        .all()
    )


def toggle_relation(existing, add_obj, count_query):
    """点赞/收藏/关注等开关型关系的通用处理。

    existing: 已存在的关联记录（None 表示尚未建立）。
    add_obj: 未建立时新增的关联记录实例。
    count_query: 用于统计当前关联数量的查询（如 CardLike.query.filter_by(card_id=...)）。
    返回 (now_active, count)。
    """
    if existing:
        db.session.delete(existing)
        now_active = False
    else:
        db.session.add(add_obj)
        now_active = True
    db.session.commit()
    count = count_query.count()
    return now_active, count


# ---------------------------------------------------------------------------
# 响应与鉴权相关的通用逻辑
# ---------------------------------------------------------------------------
def is_xhr() -> bool:
    """当前请求是否期望 JSON 响应（XHR 或 /api/ 前缀，由 before_request 写入 g.want_json）。"""
    return bool(getattr(g, "want_json", False))


def respond(redirect_url, *, ok=True, flash_msg=None, flash_cat="info", status=200, **json_payload):
    """统一响应：JSON 请求返回 JSON；普通表单请求 flash 后跳转。

    json_payload 原样放进 JSON 体（自带 ok 字段），用于携带 action/state/count 等字段。
    """
    if is_xhr():
        return jsonify(ok=ok, **json_payload), status
    if flash_msg:
        flash(flash_msg, flash_cat)
    return redirect(redirect_url)


def ensure_owner_or_admin(owner_id, message="无权访问该资源"):
    """资源归属校验：非作者且非超级管理员则 403。"""
    if int(owner_id) != current_user.id and not current_user.is_super_admin:
        abort(403, description=message)


_RATE_LIMITS = {}  # "scope:key" -> [timestamp, ...]


def rate_hit(scope, limit=5, per=60, key=None):
    """进程内限流：记录一次命中并返回是否已超过限制（True=被限流）。

    scope 为限流维度（如 "teahouse_post"）；key 缺省取当前用户 id，未登录取客户端 IP。
    单进程部署足够；多进程/多机请迁移到 Redis。
    """
    if key is None:
        key = current_user.id if current_user.is_authenticated else (request.remote_addr or "anon")
    rk = f"{scope}:{key}"
    now = time.time()
    hits = _RATE_LIMITS.setdefault(rk, [])
    hits[:] = [t for t in hits if now - t < per]
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False
