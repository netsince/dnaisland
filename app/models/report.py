from ..extensions import db


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    target_type = db.Column(db.String(20), nullable=False)  # card | comment | user
    target_id = db.Column(db.String(36), nullable=False)  # card.id / comment.id / user.id
    reason = db.Column(db.String(50), nullable=False)  # 举报原因分类
    detail = db.Column(db.Text, server_default="")
    status = db.Column(db.String(20), server_default="pending", nullable=False, index=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    handled_at = db.Column(db.DateTime, nullable=True)
    handled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    reporter = db.relationship("User", foreign_keys=[reporter_id], backref="reports_made")
    handler = db.relationship("User", foreign_keys=[handled_by], backref="reports_handled")
