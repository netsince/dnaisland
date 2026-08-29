import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, CardImage
from app.models.user import User
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
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"), (
        f"🧨 测试连到了非 SQLite 数据库！{app.config['SQLALCHEMY_DATABASE_URI']}"
    )
    # 推荐得分缓存是模块级全局，跨测试/实例会残留，需清空以免污染。
    from app.routes import main as main_mod

    main_mod._FEATURED_SCORE_CACHE.clear()
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _mk_card(author, id_, name, has_image=False, slot="portrait"):
    """建一张 approved 角色卡；has_image=True 时挂一张封面图。

    设一定 view_count 使热度分为正，确保加权随机能抽出（而非仅靠纯随机名额）。
    """
    card = Card(
        id=id_, author_id=author.id, name=name, persona="P", view_count=100
    )
    card.status = "approved"
    db.session.add(card)
    db.session.flush()
    if has_image:
        db.session.add(
            CardImage(
                card_id=id_,
                slot=slot,
                data="data:image/png;base64,AAAA",
            )
        )
    db.session.flush()
    return card


def test_swipe_only_returns_cards_with_cover(app, client):
    """GET /api/v1/cards/swipe 只返回有封面的卡（无图卡被后端过滤）。"""
    with app.app_context():
        author = User(username="sw_author", nickname="作者", email="sw_a@example.com")
        author.set_password("pass123")
        db.session.add(author)
        db.session.flush()
        # 有图卡
        _mk_card(author, "sw-with-img-1", "有图卡1", has_image=True, slot="portrait")
        _mk_card(author, "sw-with-img-2", "有图卡2", has_image=True, slot="landscape")
        # 无图卡（应被过滤）
        _mk_card(author, "sw-no-img-1", "无图卡1", has_image=False)
        db.session.commit()

    r = client.get("/api/v1/cards/swipe")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    names = [c["name"] for c in body["data"]]
    # 无图卡必须不在结果里
    assert "无图卡1" not in names
    # 有图卡在结果里（单批默认 12 张，足够容纳全部有图卡）
    assert "有图卡1" in names
    assert "有图卡2" in names
    # 返回的卡都带 covers（服务端已填路径）
    for c in body["data"]:
        assert c["covers"], f"卡 {c['id']} 应有 covers"


def test_swipe_exclude_deduplicates(app, client):
    """exclude 参数按 UUID 字符串去重（此前按 int 处理导致不生效）。"""
    with app.app_context():
        author = User(username="sw_author2", nickname="作者2", email="sw_a2@example.com")
        author.set_password("pass123")
        db.session.add(author)
        db.session.flush()
        _mk_card(author, "sw-ex-a", "排除卡A", has_image=True)
        _mk_card(author, "sw-ex-b", "保留卡B", has_image=True)
        db.session.commit()

    # 排除 sw-ex-a：结果里不能有「排除卡A」，但保留卡B仍可出现。
    r = client.get("/api/v1/cards/swipe?exclude=sw-ex-a")
    assert r.status_code == 200
    body = r.get_json()
    names = [c["name"] for c in body["data"]]
    assert "排除卡A" not in names
    assert "保留卡B" in names
