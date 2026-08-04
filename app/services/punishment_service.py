"""处罚与申诉：Web 与 App 共用的查询 / 提交逻辑。"""

from ..extensions import db
from ..models import Punishment
from ..models.punishment import APPEAL_PENDING, PUNISHMENT_TYPES
from ..services.notification_service import notify_super_admins


def my_punishments_list(user_id: int):
    """返回某用户的处罚明细（按时间倒序）。Web 与 App 共用。"""
    return (
        Punishment.query.filter_by(user_id=user_id)
        .order_by(Punishment.created_at.desc())
        .all()
    )


def submit_punishment_appeal(viewer, punishment, reason):
    """提交对处罚的申诉（每个处罚仅可申诉一次）。

    返回 (punishment, error)：error 为非空字符串表示校验失败（无权限 / 不可申诉 /
    理由为空），punishment 为更新后的对象；仅把变更加入会话，由调用方提交已在函数内完成。
    """
    if punishment.user_id != viewer.id:
        return None, "无权限"
    if not punishment.can_appeal:
        return None, "该处罚不可申诉或你已提交过申诉"
    reason = (reason or "").strip()
    if not reason:
        return None, "请填写申诉理由"
    punishment.appealed = True
    punishment.appeal_reason = reason
    punishment.appeal_status = APPEAL_PENDING
    punishment.appeal_at = db.func.now()
    db.session.commit()
    notify_super_admins(
        f'用户 {viewer.nickname} 对处罚「{PUNISHMENT_TYPES.get(punishment.type, punishment.type)}」提交了申诉，请到「处罚申诉」处理。',
        type_="punish",
    )
    return punishment, None
