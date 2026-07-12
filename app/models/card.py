from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db


class Card(db.Model):
    __tablename__ = "cards"

    # 复用客户端 TA.id (UUID)，保证导入导出身份一致
    id = db.Column(db.String(36), primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(20), server_default="无性")
    persona = db.Column(db.Text, nullable=False, server_default="")
    intro = db.Column(db.Text, server_default="")
    opening = db.Column(db.Text, server_default="")
    original_link = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )
    status = db.Column(db.String(20), server_default="published")
    view_count = db.Column(db.Integer, server_default="0")


class CardTag(db.Model):
    __tablename__ = "card_tags"

    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), primary_key=True)
    tag = db.Column(db.String(50), primary_key=True, index=True)


class CardDialogueStyle(db.Model):
    __tablename__ = "card_dialogue_styles"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False)
    turn_index = db.Column(db.Integer, nullable=False, server_default="0")
    user_text = db.Column(db.Text, server_default="")
    assistant_text = db.Column(db.Text, server_default="")


class CardImage(db.Model):
    __tablename__ = "card_images"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False)
    slot = db.Column(db.String(20), nullable=False)  # square | landscape | portrait
    data = db.Column(LONGTEXT, nullable=False)  # base64 data URI
