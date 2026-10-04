"""编辑角色卡时，App 回传的「既有图相对路径」必须被当成"这张图没改"。

用户反馈：App 编辑角色卡基本上无法使用 —— 图片显示不出来，而且**提交必然失败**。

根因（本文件就是回归证据）：
* App 的卡详情接口 `/api/v1/cards/<id>` 里 `images` 是 `{slot: "/card-image/<id>/<slot>"}`
  —— 相对路径，不是 data URL（接口刻意不回传 base64，太大）；
* App 编辑时把**这个路径原样**放进 `images` 回传；
* 而 `update_card_from_payload` 走的是 `data_url_to_bytes_and_mime`，它只认 `data:` 开头，
  对路径直接 `raise ValueError("无效的图片数据")` → 编辑接口 400；
* 即便不报错，编辑是**覆盖式**替换图片，图片也会被写坏。

修法：把「本卡自己的 `/card-image/<card.id>/<slot>` 路径」识别为"未改动"，用库里存的
data URL **原样保留**（顺带避免每次编辑都重编码掉一次画质）。其它取值（data URL、字节、
以及别人的/伪造的路径）一律走原有校验，不能被这条放宽。
"""

import base64
import io
import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, CardImage
from app.models.user import User
from app.services.card_edit_service import update_card_from_payload
from PIL import Image
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


def _square_webp_data_url(color=(30, 120, 200), size=64) -> str:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=90)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def _card_with_image():
    u = User(username="edit_owner", nickname="作者", email="eo@example.com", password_hash="x")
    db.session.add(u)
    db.session.commit()
    card = Card(
        id="card-edit-1",
        author_id=u.id,
        name="原卡名",
        persona="人设",
        status="approved",
    )
    db.session.add(card)
    db.session.commit()
    stored = _square_webp_data_url()
    db.session.add(CardImage(card_id=card.id, slot="square", data=stored))
    db.session.commit()
    return card, stored


def test_app_style_relative_path_keeps_the_image(app):
    """App 的 payload（既有图 = 相对路径）必须成功，且图片原样保留。"""
    with app.app_context():
        card, stored = _card_with_image()
        payload = {
            "name": "改过的卡名",
            "persona": "人设",
            "tags": ["测试"],
            "images": {"square": f"/card-image/{card.id}/square"},
        }
        error = update_card_from_payload(card, payload)
        assert error is None, f"App 式的编辑不该失败，实际返回：{error!r}"

        assert card.name == "改过的卡名"
        assert card.status == "pending", "编辑后应重新提审"
        row = CardImage.query.filter_by(card_id=card.id, slot="square").one()
        assert row.data == stored, "既有图必须原样保留（连字节都不该变，避免反复编辑掉画质）"


def test_relative_path_does_not_trigger_aspect_validation(app):
    """存量比例不合规的老卡：只回传路径（=没改图）不该被比例校验卡住。"""
    with app.app_context():
        card, stored = _card_with_image()
        # 造一张「宽高比不合 1:1」的存量图，模拟历史遗留数据
        img = Image.new("RGB", (120, 60), (200, 80, 80))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=90)
        bad = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
        db.session.query(CardImage).filter_by(card_id=card.id, slot="square").one().data = bad
        db.session.commit()

        error = update_card_from_payload(
            card, {"name": "改名", "images": {"square": f"/card-image/{card.id}/square"}}
        )
        assert error is None, "回传路径视为未改动，不该触发比例校验"
        assert CardImage.query.filter_by(card_id=card.id, slot="square").one().data == bad


def test_foreign_or_bogus_path_is_still_rejected(app):
    """放宽只针对「本卡自己的路径」：别人的路径 / 伪造路径仍按无效图片处理。"""
    with app.app_context():
        card, _ = _card_with_image()
        for bogus in (
            "/card-image/other-card/square",
            "/card-image/card-edit-1/landscape",  # 槽位对不上
            "/card-image/card-edit-1/square?x=1",
            "/not-an-image",
        ):
            error = update_card_from_payload(card, {"images": {"square": bogus}})
            assert error, f"{bogus!r} 应被拒绝，实际返回 {error!r}"


def test_new_data_url_still_replaces_and_validates(app):
    """正常替换（data URL）仍然生效，比例不合规仍然被拒。"""
    with app.app_context():
        card, stored = _card_with_image()
        replacement = _square_webp_data_url(color=(10, 200, 10))
        assert update_card_from_payload(card, {"images": {"square": replacement}}) is None
        assert CardImage.query.filter_by(card_id=card.id, slot="square").one().data != stored

        # 1:1 槽位塞一张 2:1 的图 → 必须被拒
        wide = Image.new("RGB", (200, 100), (0, 0, 0))
        buf = io.BytesIO()
        wide.save(buf, format="WEBP", quality=90)
        wide_url = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
        error = update_card_from_payload(card, {"images": {"square": wide_url}})
        assert error and "比例" in error, f"比例不合规应被拒，实际：{error!r}"


def test_omitting_images_clears_them(app):
    """不带 images（网页端"删除图片"的语义）仍然清空 —— 覆盖式语义不能被改坏。"""
    with app.app_context():
        card, _ = _card_with_image()
        assert update_card_from_payload(card, {"name": "无图"}) is None
        assert CardImage.query.filter_by(card_id=card.id).count() == 0


def test_app_detail_shape_matches_the_keep_convention(app):
    """守住约定：卡详情接口给的就是 `/card-image/<id>/<slot>`，与上面的识别规则一致。

    两处一旦不一致（比如接口改成返回别的形式），这条会红。
    """
    from app.routes.card_lists import card_detail_core

    with app.app_context():
        card, _ = _card_with_image()
        owner = db.session.get(User, card.author_id)
        _card, data, error_code = card_detail_core(card.id, owner)
        assert error_code is None
        assert data["images"] == {"square": f"/card-image/{card.id}/square"}
        assert json.dumps(data["images"])  # 可 JSON 序列化
