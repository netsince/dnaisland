"""角色卡评论的点赞 / 置顶 / 删除：Web 与 App 共用的核心逻辑。

Web 端路由与 App 端接口都调用这里的函数，保证权限口径、点赞通知、置顶归属等
行为完全一致。
"""
from ..extensions import db
from ..models import Card, CommentLike
from ..services.notification_service import notify
from ..utils import toggle_relation


def toggle_comment_like(viewer, comment):
    """切换 viewer 对 comment 的点赞状态。

    返回 (is_now_liked, new_count)。若点赞成功且评论作者不是自己，则通知评论作者。
    """
    is_now_liked, new_count = toggle_relation(
        CommentLike.query.filter_by(
            user_id=viewer.id, comment_id=comment.id
        ).first(),
        CommentLike(user_id=viewer.id, comment_id=comment.id),
        CommentLike.query.filter_by(comment_id=comment.id),
    )
    if is_now_liked and comment.user_id != viewer.id:
        card = db.session.get(Card, comment.card_id) if comment.card_id else None
        if card:
            notify(
                user_id=comment.user_id,
                message=f"{viewer.display_name} 点赞了你在《{card.name}》下的评论",
                type_="comment_like",
                related_card_id=card.id,
            )
    return is_now_liked, new_count


def pin_comment(viewer, comment):
    """置顶/取消置顶一条评论（仅该卡作者或超管）。

    返回 error（非空表示无权限）；comment.is_pinned 会被原地切换。
    """
    card = db.session.get(Card, comment.card_id) if comment.card_id else None
    if not (viewer.is_super_admin or (card and viewer.id == card.author_id)):
        return "无权置顶此评论"
    comment.is_pinned = not comment.is_pinned
    db.session.commit()
    return None


def delete_comment(viewer, comment):
    """删除一条评论（仅评论作者或超管）。返回 error（非空表示无权限）。"""
    if not (viewer.is_super_admin or viewer.id == comment.user_id):
        return "无权删除此评论"
    db.session.delete(comment)
    db.session.commit()
    return None
