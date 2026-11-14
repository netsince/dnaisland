"""角色卡「对话策略」（strategy）：社区卡携带客户端那组可选提示词策略字段。

客户端把整组策略当成一个 JSON 对象发送：所有键都可选，未设置时**整个键都不发**。
社区侧因此有两条硬约束：
  1. 发布/编辑能收下它，详情接口能原样读回；
  2. 编辑时只有 payload 里出现 strategy 键才覆盖 —— 网页端没动这一项时不能把它清空。
脏数据（不是 dict、非法 JSON 字符串、空对象）一律落成 NULL，但发布本身不能失败。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.services.card_edit_service import update_card_from_payload
from app.services.card_publish_service import (
    create_card_from_payload,
    strategy_to_dict,
)
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
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _author(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()
    return u


# 客户端实际会发的那组字段（键全可选），用来确认往返不丢键、不改类型。
STRATEGY = {
    "pov": "third",
    "narrativeStructure": "threeAct",
    "structureGoal": "三章内破冰",
    "pacing": "scene",
    "tensionCurve": "wave",
    "reveal": "layered",
    "revealMaxPerTurn": 2,
    "conflictPrimary": "interpersonal",
    "conflictSecondary": "inner",
    "endingStyle": "open",
    "closureHook": True,
    "ambientEnabled": True,
    "ambientTime": "深夜",
    "ambientWeather": "雨",
    "npcMax": 2,
    "aiPlaysNpc": True,
    "emotionTone": "sweet",
    "emotionIntensity": 3,
    "addressMode": "fixed",
    "addressValue": "老师",
    "catchphrases": ["哼"],
    "anchorTraits": ["怕黑"],
    "anchorVoiceSample": "你又要走了？",
    "sentenceLength": 3,
    "metaphorPerParagraph": 1,
    "humorLevel": 2,
    "dialogueRatio": 60,
    "motifName": "雨",
    "motifEveryN": 4,
}


def _login(client, name):
    client.post("/auth/login", data={"identifier": name, "password": "pw"})


def test_publish_with_strategy_reads_back_from_detail(app, client):
    """发布带 strategy → App 详情接口能读回同样的 dict（客户端按这个形状接收）。"""
    with app.app_context():
        _author("s1")
    _login(client, "s1")

    resp = client.post("/api/v1/cards/publish", json={"name": "带策略的卡", "strategy": STRATEGY})
    assert resp.status_code == 201
    card_id = resp.get_json()["data"]["id"]

    detail = client.get(f"/api/v1/cards/{card_id}")
    assert detail.status_code == 200
    assert detail.get_json()["data"]["strategy"] == STRATEGY


def test_publish_without_strategy_returns_null(app, client):
    """发布不带 strategy → 详情里为 null（客户端据此整个键都不发）。"""
    with app.app_context():
        _author("s2")
    _login(client, "s2")

    resp = client.post("/api/v1/cards/publish", json={"name": "没有策略的卡", "persona": "凡人"})
    assert resp.status_code == 201
    card_id = resp.get_json()["data"]["id"]

    detail = client.get(f"/api/v1/cards/{card_id}")
    assert detail.get_json()["data"]["strategy"] is None


def test_publish_accepts_json_string_strategy(app):
    """JSON 字符串形式的 strategy 也照收（网页表单回传的就是字符串）。"""
    with app.app_context():
        author = _author("s3")
        card, error = create_card_from_payload(
            author, {"name": "字符串策略", "strategy": json.dumps(STRATEGY, ensure_ascii=False)}
        )
        assert card is not None and error is None
        db.session.commit()
        assert strategy_to_dict(card.strategy) == STRATEGY


def test_edit_without_strategy_key_keeps_existing(app):
    """编辑 payload 里没有 strategy 键 → 原值保留（不被清空）。"""
    with app.app_context():
        author = _author("s4")
        card, error = create_card_from_payload(author, {"name": "编辑保留", "strategy": STRATEGY})
        assert card is not None and error is None
        db.session.commit()

        # 只改人设：payload 完全不提 strategy
        assert update_card_from_payload(card, {"persona": "只改人设"}) is None
        assert strategy_to_dict(card.strategy) == STRATEGY


def test_edit_with_strategy_key_overwrites(app):
    """payload 里出现 strategy 键 → 覆盖（含显式传 None 表示清除）。"""
    with app.app_context():
        author = _author("s5")
        card, error = create_card_from_payload(author, {"name": "编辑覆盖", "strategy": STRATEGY})
        assert card is not None and error is None
        db.session.commit()

        assert update_card_from_payload(card, {"strategy": {"pov": "first"}}) is None
        assert strategy_to_dict(card.strategy) == {"pov": "first"}
        assert update_card_from_payload(card, {"strategy": None}) is None
        assert card.strategy is None


def test_web_edit_form_without_strategy_field_keeps_existing(app, client):
    """网页编辑页没动「提示词策略」块（不提交 strategy_json）→ 存量策略不被清空。

    这是「网页端没改这一项不能弄丢」的端到端验证：表单 -> payload -> 服务层 -> 详情回读。
    """
    with app.app_context():
        _author("s6")
    _login(client, "s6")
    resp = client.post("/api/v1/cards/publish", json={"name": "网页编辑保留", "strategy": STRATEGY})
    card_id = resp.get_json()["data"]["id"]

    page = client.post(
        f"/my/card/{card_id}/edit",
        data={"name": "网页编辑保留", "gender": "无性", "persona": "改了人设", "tags": ""},
        follow_redirects=True,
    )
    assert page.status_code == 200
    detail = client.get(f"/api/v1/cards/{card_id}")
    assert detail.get_json()["data"]["strategy"] == STRATEGY


def test_web_edit_form_with_strategy_field_saves_it(app, client):
    """网页编辑页改动了文本框（提交 strategy_json）→ 以提交内容为准。"""
    with app.app_context():
        _author("s7")
    _login(client, "s7")
    resp = client.post("/api/v1/cards/publish", json={"name": "网页改策略"})
    card_id = resp.get_json()["data"]["id"]

    page = client.post(
        f"/my/card/{card_id}/edit",
        data={
            "name": "网页改策略",
            "gender": "无性",
            "persona": "",
            "tags": "",
            "strategy_json": json.dumps({"pov": "second"}, ensure_ascii=False),
        },
        follow_redirects=True,
    )
    assert page.status_code == 200
    detail = client.get(f"/api/v1/cards/{card_id}")
    assert detail.get_json()["data"]["strategy"] == {"pov": "second"}


def test_web_edit_page_renders_strategy_textbox(app, client):
    """网页编辑页把当前策略美化后放进文本框（模板与 prefill 的接线）。"""
    with app.app_context():
        _author("s8")
    _login(client, "s8")
    resp = client.post(
        "/api/v1/cards/publish", json={"name": "渲染策略", "strategy": {"pov": "third"}}
    )
    card_id = resp.get_json()["data"]["id"]

    page = client.get(f"/my/card/{card_id}/edit")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "提示词策略（高级）" in body
    # 美化后的 JSON 落进文本框（tojson 只转义 < > & '，双引号保持原样）
    assert '"pov"' in body and '"third"' in body


def test_web_publish_page_renders_empty_strategy_textbox(app, client):
    """发布页（prefill 为 None）也要能渲染这一块：策略为空、不报模板错。"""
    with app.app_context():
        _author("s9")
    _login(client, "s9")

    page = client.get("/publish/edit")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "提示词策略（高级）" in body
    assert "const strategyInitialDict = null;" in body


@pytest.mark.parametrize(
    "idx,raw",
    [
        (0, ["pov", "third"]),  # 不是 dict（数组）
        (1, "not-json{"),  # 非法 JSON 字符串
        (2, ""),  # 空串
        (3, {}),  # 空对象
        (4, 123),  # 标量
        (5, {"nested": {1, 2}}),  # 含不可 JSON 序列化的值
    ],
)
def test_dirty_strategy_becomes_null_but_publish_succeeds(app, idx, raw):
    """脏数据 → 存成 null，发布本身不失败。"""
    with app.app_context():
        author = _author(f"dirty{idx}")
        card, error = create_card_from_payload(author, {"name": f"脏数据{idx}", "strategy": raw})
        assert error is None
        assert card is not None
        db.session.commit()
        assert card.strategy is None
