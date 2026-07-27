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
    images = db.deferred(db.Column(LONGTEXT, nullable=True))  # JSON 数组：base64 data URL 列表
    reference_images = db.deferred(db.Column(LONGTEXT, nullable=True))  # JSON 数组：参考图的 WebP Data URL 列表
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


class GenerationTask(db.Model):
    """生图异步任务：/generate 仅创建任务并立即返回 task_id，实际生图由后台线程完成。

    状态机：pending -> processing -> succeeded / failed。
    任务状态持久化于数据库，因此刷新/重开页面、跨 gunicorn worker 都能继续轮询跟踪。
    列表接口只返回 pending/processing，绝不包含历史任务。
    """

    __tablename__ = "generation_tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    prompt = db.Column(db.Text, nullable=False)
    model_id = db.Column(
        db.Integer, db.ForeignKey("generation_models.id"), nullable=True
    )
    model_name = db.Column(db.String(120), nullable=True)
    size = db.Column(db.String(20), nullable=True)
    count = db.Column(db.Integer, nullable=False, default=1)
    references_count = db.Column(db.Integer, nullable=False, default=0)
    # 参考图原始数据：JSON 数组 [{filename, mimetype, data_b64}]，后台线程还原为字节
    reference_data = db.Column(LONGTEXT, nullable=True)
    result_log_id = db.Column(db.Integer, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )

    def reference_list(self):
        if not self.reference_data:
            return []
        try:
            return json.loads(self.reference_data)
        except (ValueError, TypeError):
            return []

