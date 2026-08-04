"""举报目标解析：统一管理 card / comment / user / teapost 四种类型的解析逻辑。

替代 user.py 中 resolve_report_target 与 admin.py 中 report_detail 各自内联的解析，
返回统一的描述体：
    {
        "id": str,        # 规范化的目标 id（用于去重/查询）
        "display": str,    # 简短文案（用于通知/列表）
        "url": str,        # 目标详情页链接
        "snippet": str,    # 较长文本（用于管理后台预览）
    }
找不到目标时返回 None。
"""

from flask import url_for

# 举报目标类型与原因：Web 与 App 共用同一份定义，避免两端口径分叉。
REPORT_TARGETS = ("card", "comment", "user", "teapost")
REPORT_REASONS = [
    ("spam", "垃圾广告 / 刷屏"),
    ("porn", "色情低俗"),
    ("violence", "暴力血腥"),
    ("politics", "违规政治内容"),
    ("abuse", "人身攻击 / 辱骂"),
    ("copyright", "侵犯版权"),
    ("other", "其他"),
]


def describe_report_target(target_type: str, raw_id: str):
    from ..models import Card, Comment, TeaPost

    target_type = (target_type or "").strip()
    if not raw_id:
        return None

    if target_type == "card":
        card = db_get(Card, raw_id)
        if not card:
            return None
        author = card.author
        return {
            "id": str(card.id),
            "display": f"角色卡《{card.name or '未命名'}》",
            "url": url_for("user.card_detail", card_id=card.id),
            "snippet": (
                f"名称：{card.name}\n"
                f"作者：{author.nickname if author else '未知'}\n"
                f"简介：{card.intro or ''}"
            ),
        }

    if target_type == "comment":
        c = db_get(Comment, raw_id)
        if not c:
            return None
        author = c.author
        card = db_get(Card, c.card_id) if c.card_id else None
        return {
            "id": str(c.id),
            "display": f"{author.nickname if author else '某用户'} 对角色卡《{card.name if card else '?'}》的评论",
            "url": url_for("user.card_detail", card_id=c.card_id) + f"#comment-{c.id}",
            "snippet": "评论：\n" + (c.content or ""),
        }

    if target_type == "teapost":
        p = db_get(TeaPost, raw_id)
        if not p:
            return None
        author = p.author
        return {
            "id": str(p.id),
            "display": f"{author.nickname if author else '某用户'} 的茶馆帖子",
            "url": url_for("teahouse.post_detail", post_id=p.id),
            "snippet": (p.content or "")[:500],
        }

    if target_type == "user":
        u = resolve_user(raw_id)
        if not u:
            return None
        return {
            "id": str(u.id),
            "display": f"用户 {u.nickname or u.username}",
            "url": url_for("user.profile", username=u.username),
            "snippet": (f"昵称：{u.nickname}\n地区：{u.location or ''}\n简介：{u.bio or ''}"),
        }

    return None


def resolve_report_target(target_type: str, raw_id: str):
    """兼容旧调用方：返回 (canonical_id, display, target_url) 三元组。"""
    d = describe_report_target(target_type, raw_id)
    if not d:
        return None
    return d["id"], d["display"], d["url"]


def db_get(model, raw_id):
    from ..extensions import db

    # 主键可能是整数（Comment / TeaPost / User），也可能是字符串 UUID（Card）。
    # 先尝试按整数解析，失败则退回原始字符串，避免 Card 的 UUID 主键被 int() 拒绝。
    try:
        pk = int(raw_id)
    except (ValueError, TypeError):
        pk = raw_id
    try:
        return db.session.get(model, pk)
    except (ValueError, TypeError):
        return None


def resolve_user(raw_id):
    from ..extensions import db
    from ..models import User

    try:
        return db.session.get(User, int(raw_id))
    except (ValueError, TypeError):
        return User.query.filter_by(username=raw_id).first()


def submit_report(viewer, target_type, canonical_id, reason, detail, display):
    """提交一条举报（Web 与 App 共用核心逻辑）。

    返回 (ok, error)：ok 为 True 表示提交成功；error 为非空字符串表示校验失败
    （未选原因 / 重复举报）。调用方需先自行完成「不能举报自己」「目标解析」等前置校验。
    """
    valid_reasons = {r[0] for r in REPORT_REASONS}
    if reason not in valid_reasons:
        return False, "请选择举报原因"
    from ..models import Report

    if Report.query.filter_by(
        reporter_id=viewer.id,
        target_type=target_type,
        target_id=canonical_id,
        status="pending",
    ).first():
        return False, "你已经举报过该对象，请勿重复提交"
    from ..extensions import db

    db.session.add(
        Report(
            reporter_id=viewer.id,
            target_type=target_type,
            target_id=canonical_id,
            reason=reason,
            detail=detail,
        )
    )
    db.session.commit()
    from ..services.notification_service import notify_super_admins

    notify_super_admins(f"收到一条对{target_type}的举报：{display}", type_="report")
    return True, None
