from ..extensions import db

# 工单状态
TICKET_OPEN = "open"          # 待处理（新建 / 用户新回复后回到此状态）
TICKET_REPLIED = "replied"    # 已回复（管理员回复后）
TICKET_CLOSED = "closed"      # 已关闭

TICKET_STATUSES = [TICKET_OPEN, TICKET_REPLIED, TICKET_CLOSED]

# 消息发送方角色
MSG_ROLE_USER = "user"
MSG_ROLE_ADMIN = "admin"


class TicketCategory(db.Model):
    """工单类别（后台可增删改）。"""

    __tablename__ = "ticket_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, server_default="0", nullable=False)
    enabled = db.Column(db.Boolean, server_default="1", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Ticket(db.Model):
    """工单：用户提交问题，与后台逐条对话。"""

    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    category_id = db.Column(
        db.Integer, db.ForeignKey("ticket_categories.id"), nullable=True
    )
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(
        db.String(20), server_default=TICKET_OPEN, nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False, index=True
    )
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )
    closed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    category = db.relationship("TicketCategory")
    messages = db.relationship(
        "TicketMessage",
        backref="ticket",
        order_by="TicketMessage.created_at.asc(), TicketMessage.id.asc()",
        cascade="all, delete-orphan",
    )

    @property
    def last_message(self):
        return self.messages[-1] if self.messages else None

    @property
    def category_name(self):
        return self.category.name if self.category else "未分类"


class TicketMessage(db.Model):
    """工单对话消息（用户 / 管理员来回）。"""

    __tablename__ = "ticket_messages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    sender_role = db.Column(
        db.String(10), server_default=MSG_ROLE_USER, nullable=False
    )
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    sender = db.relationship("User", foreign_keys=[sender_id])