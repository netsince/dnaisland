from ..extensions import db


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(30), server_default="system", nullable=False)
    message = db.Column(db.Text, nullable=False)
    related_card_id = db.Column(db.String(36), nullable=True)
    is_read = db.Column(db.Boolean, server_default="0", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
