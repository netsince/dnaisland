"""生图（AI 绘图）相关模型。

- GenerationModel: 管理员配置的可用模型（调用名 + 展示名 + 每图积分）。
- GenerationLog:    每次生图记录（提示词、参数、产出图 base64、消耗积分），用户与管理员均可审阅。
"""

import json

from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db


class GenerationModel(db.Model):
    """管理员配置的生图模型（OpenAI 格式通道下的一可选模型）。"""

    __tablename__ = "generation_models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)  # 调用名，如 gpt-image-1
    display_name = db.Column(db.String(120), nullable=False)  # 前端展示名
    points_per_image = db.Column(
        db.Integer, nullable=False, server_default="0", default=0
    )  # 每张图消耗的积分数
    enabled = db.Column(
        db.Boolean, nullable=False, server_default="1", default=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<GenerationModel {self.name}>"


class GenerationLog(db.Model):
    """一次生图请求的完整记录（含产出图，便于用户回看与管理员审阅）。"""

    __tablename__ = "generation_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    model_id = db.Column(
        db.Integer, db.ForeignKey("generation_models.id"), nullable=True, index=True
    )
    model_name = db.Column(db.String(120), nullable=True)  # 冗余展示名
    prompt = db.Column(db.Text, nullable=False)
    size = db.Column(db.String(20), nullable=True)  # 如 1024x1024 / None=auto
    count = db.Column(db.Integer, nullable=False, default=1)  # 请求张数
    references_count = db.Column(
        db.Integer, nullable=False, server_default="0", default=0
    )
    status = db.Column(
        db.String(10), nullable=False, default="success"
    )  # success / partial / failed
    images = db.Column(LONGTEXT, nullable=True)  # JSON 数组：base64 data URL 列表
    reference_images = db.Column(LONGTEXT, nullable=True)  # JSON 数组：参考图的 WebP Data URL 列表
    points_spent = db.Column(
        db.Integer, nullable=False, server_default="0", default=0
    )
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    user = db.relationship("User", backref="generation_logs")
    model = db.relationship("GenerationModel", backref="logs")

    def image_list(self):
        if not self.images:
            return []
        try:
            data = json.loads(self.images)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def reference_image_list(self):
        if not self.reference_images:
            return []
        try:
            data = json.loads(self.reference_images)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

