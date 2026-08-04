"""关注/取关的共享逻辑：Web 路由与 App API 共用。

约定：follower 为当前操作人（User 实例），following 为被关注目标（User 实例）。
"""
from ..models import UserFollow
from ..services.notification_service import notify
from ..utils import toggle_relation


def toggle_user_follow(viewer, target):
    """切换 viewer 对 target 的关注状态。

    返回 (now_following, error)：
    - error == "self" 表示不能关注自己，now_following 为 None；
    - 否则 now_following 为新的关注布尔状态（关注成功时已发送关注通知）。

    注意：本函数只把变更加入会话，由调用方负责 db.session.commit()。
    """
    if target.id == viewer.id:
        return None, "self"
    now_following, _ = toggle_relation(
        UserFollow.query.filter_by(
            follower_id=viewer.id, following_id=target.id
        ).first(),
        UserFollow(follower_id=viewer.id, following_id=target.id),
        UserFollow.query.filter_by(following_id=target.id),
    )
    if now_following:
        notify(target.id, f"{viewer.nickname} 关注了你", type_="follow")
    return now_following, None
