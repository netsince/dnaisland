from ..extensions import db
from ..models import Notification


def notify(user_id: int, message: str, type_: str = "system", related_card_id=None):
    """创建一条通知。"""
    n = Notification(
        user_id=user_id,
        message=message,
        type=type_,
        related_card_id=related_card_id,
    )
    db.session.add(n)
    db.session.flush()
    return n


def notify_super_admins(message: str, type_: str = "system"):
    """向所有超级管理员发送通知，替代各处重复的 for admin in ...: notify(...) 循环。"""
    from ..models import User

    for admin in User.query.filter_by(role="super_admin").all():
        notify(admin.id, message, type_=type_)


def unread_count(user_id: int) -> int:
    return Notification.query.filter_by(user_id=user_id, is_read=False).count()


def mark_all_read(user_id: int):
    Notification.query.filter_by(user_id=user_id, is_read=False).update(
        {"is_read": True}
    )
    db.session.commit()
