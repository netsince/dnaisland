from ..extensions import db


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(30), server_default="system", nullable=False)
    message = db.Column(db.Text, nullable=False)
    # ---- 关联对象 ----
    #
    # 早先只有 related_card_id（角色卡）。后来要支持世界卡等其它元素，于是加了
    # 通用的一对 related_type + related_id。
    #
    # **related_card_id 保留**：已发布的老客户端仍在读它做跳转，删了会断。
    # 写入时两者保持同步（见 notification_service.notify）：
    #   related_type="card"  <=>  related_card_id 有值
    related_card_id = db.Column(db.String(36), nullable=True)
    # 关联对象类型：card | world | ...（新增元素时在这里加一个值即可）
    related_type = db.Column(db.String(20), nullable=True)
    # 关联对象 id（角色卡是 UUID，世界卡也是 UUID）
    related_id = db.Column(db.String(36), nullable=True)
    is_read = db.Column(db.Boolean, server_default="0", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
