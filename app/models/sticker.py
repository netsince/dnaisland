"""表情包（Sticker）模型：系列 + 表情，图片以 base64 内联存储。"""
from ..extensions import db


class StickerSeries(db.Model):
    __tablename__ = "sticker_series"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, server_default=db.text("0"))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    stickers = db.relationship(
        "Sticker",
        backref="series",
        cascade="all, delete-orphan",
        order_by="Sticker.sort_order",
    )


class Sticker(db.Model):
    __tablename__ = "stickers"

    id = db.Column(db.Integer, primary_key=True)
    # 管理员配置的表情 ID（唯一），用于内容 token [sticker:CODE]
    code = db.Column(db.String(60), unique=True, nullable=False)
    series_id = db.Column(
        db.Integer,
        db.ForeignKey("sticker_series.id", ondelete="CASCADE"),
        nullable=False,
    )
    # base64 data URL，内联存储（MySQL 下迁移为 LONGTEXT）
    image_data = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, server_default=db.text("0"))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
