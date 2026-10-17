from ..caching import TimedCache
from ..extensions import db
from ..models import Notification

# 未读计数按用户缓存 30s：角标延迟最多 30s 可接受，省去每个请求查库。
_unread_cache = TimedCache(ttl=30)


def notify(
    user_id: int,
    message: str,
    type_: str = "system",
    related_card_id=None,
    related_type=None,
    related_id=None,
):
    """创建一条通知。

    关联对象有两种传法：

    * **新写法**（推荐）：`related_type="world", related_id="<id>"`
    * **旧写法**：`related_card_id="<id>"`，等价于 `related_type="card"`

    两个字段都写：related_card_id 是给已发布的老客户端做跳转用的，删不得；
    related_type/related_id 是新口径，客户端按 type 决定跳到哪里。
    """
    if related_card_id is not None and related_type is None:
        related_type, related_id = "card", related_card_id
    if related_type == "card" and related_card_id is None:
        related_card_id = related_id
    n = Notification(
        user_id=user_id,
        message=message,
        type=type_,
        related_card_id=related_card_id,
        related_type=related_type,
        related_id=related_id,
    )
    db.session.add(n)
    db.session.flush()
    # 新通知使其未读计数缓存失效，确保角标及时更新
    _unread_cache.invalidate(user_id)
    return n


def notify_super_admins(message: str, type_: str = "system"):
    """向所有超级管理员发送通知，替代各处重复的 for admin in ...: notify(...) 循环。"""
    from ..models import User

    for admin in User.query.filter_by(role="super_admin").all():
        notify(admin.id, message, type_=type_)


def unread_count(user_id: int) -> int:
    val = _unread_cache.get(user_id)
    if val is not None:
        return val
    val = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    _unread_cache.set(user_id, val)
    return val


def mark_all_read(user_id: int):
    Notification.query.filter_by(user_id=user_id, is_read=False).update({"is_read": True})
    db.session.commit()
    _unread_cache.invalidate(user_id)


def notifications_page(user_id: int, page: int = 1, per_page: int = 20):
    """返回指定用户的通知明细分页对象（按时间倒序）。Web 与 App 共用。"""
    if page < 1:
        page = 1
    return (
        Notification.query.filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
