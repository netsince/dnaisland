"""跨路由复用的小工具函数。"""

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
