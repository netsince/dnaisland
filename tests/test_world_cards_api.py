"""社区「世界卡」接口（/api/v1/worlds）。

守两条核心约定：

* **payload 是原文**：发布进来什么样、取出去就什么样。服务端不做
  json.dumps(json.loads(...)) 那种往返 —— 那会改掉键序、空格与数字格式，
  而客户端导出/导入是靠文本比对的。
* **取原文 = 复制**：那一次请求同时记一条复制统计，同一用户对同一个世界
  每天最多 +1（明细每次都记，口径与 CardCopyStat 一致）。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard, WorldCopyStat
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
        "测试连到了非 SQLite 数据库！"
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _payload(name="幻想大陆", entries=None):
    return {
        "id": "local-world-1",
        "name": name,
        "summary": "一个用于测试的世界",
        "description": "详细说明",
        "tags": ["奇幻", "测试"],
        "forbiddenWords": ["禁词"],
        "entries": entries
        if entries is not None
        else [
            {
                "id": "e1",
                "name": "王都",
                "description": "首都",
                "type": "noun",
                "keys": ["都城"],
            },
            {
                "id": "e2",
                "name": "艾琳",
                "description": "女王",
                "type": "person",
                "gender": "female",
            },
        ],
        "archived": False,
    }


def _login(app, client, username="wc_user"):
    with app.app_context():
        u = User(username=username, nickname="用户", email=username + "@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


def _publish(client, body_text):
    """请求体就是世界 JSON 原文本身（按 UTF-8 字节发，保证字节完全一致）。"""
    return client.post(
        "/api/v1/worlds",
        data=body_text.encode("utf-8"),
        content_type="application/json",
    )


def test_publish_requires_login(app, client):
    resp = _publish(client, json.dumps(_payload(), ensure_ascii=False))
    assert resp.status_code == 401


def test_publish_fills_list_metadata(app, client):
    _login(app, client)
    resp = _publish(client, json.dumps(_payload(), ensure_ascii=False))
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["status"] == "pending"
    assert body["data"]["reused"] is False

    with app.app_context():
        world = db.session.get(WorldCard, body["data"]["id"])
        assert world.name == "幻想大陆"
        assert world.entry_count == 2
        assert world.payload_bytes > 0


def test_payload_is_stored_verbatim(app, client):
    """核心：进来什么样、出去什么样（不做 JSON 往返）。"""
    _login(app, client)
    # 刻意写成非常规格式：换行、缩进、键序、多余空格、数字写法。
    raw = (
        '{\n'
        '   "summary" : "s",\n'
        '   "name": "  空格世界  ",\n'
        '   "entries": [],\n'
        '   "tags": ["a"],\n'
        '   "n": 1.50\n'
        '}'
    )
    published = _publish(client, raw)
    assert published.get_json()["ok"] is True
    world_id = published.get_json()["data"]["id"]

    resp = client.get("/api/v1/worlds/" + world_id + "/export")
    assert resp.status_code == 200
    # 逐字节相同 —— 键序、缩进、1.50 的写法都原封不动。
    assert resp.data.decode("utf-8") == raw


def test_duplicate_publish_reuses_pending(app, client):
    _login(app, client)
    raw = json.dumps(_payload(), ensure_ascii=False)
    first = _publish(client, raw).get_json()["data"]
    second = _publish(client, raw).get_json()["data"]
    assert first["id"] == second["id"]
    assert second["reused"] is True


def test_invalid_json_rejected(app, client):
    _login(app, client)
    assert _publish(client, "not json at all").status_code == 400
    assert _publish(client, "[1, 2, 3]").status_code == 400
    assert _publish(client, json.dumps({"entries": []})).status_code == 400  # 缺 name


def test_list_carries_no_payload(app, client):
    _login(app, client)
    world_id = _publish(
        client, json.dumps(_payload(), ensure_ascii=False)
    ).get_json()["data"]["id"]
    with app.app_context():
        db.session.get(WorldCard, world_id).status = "approved"
        db.session.commit()

    body = client.get("/api/v1/worlds").get_json()
    assert body["ok"] is True
    item = body["data"]["items"][0]
    assert item["name"] == "幻想大陆"
    assert item["entry_count"] == 2
    assert item["tags"] == ["奇幻", "测试"]
    # 列表绝不能带正文。
    assert "payload" not in item
    assert "sample_entry" not in item


def test_detail_gives_names_and_one_sample(app, client):
    _login(app, client)
    world_id = _publish(
        client, json.dumps(_payload(), ensure_ascii=False)
    ).get_json()["data"]["id"]
    with app.app_context():
        db.session.get(WorldCard, world_id).status = "approved"
        db.session.commit()

    body = client.get("/api/v1/worlds/" + world_id).get_json()
    data = body["data"]
    assert data["entry_names"] == ["王都", "艾琳"]
    # 只给 1 条示例，不是全部。
    assert data["sample_entry"]["name"] == "王都"
    assert "payload" not in data


def test_export_counts_copy_once_per_day(app, client):
    _login(app, client)
    world_id = _publish(
        client, json.dumps(_payload(), ensure_ascii=False)
    ).get_json()["data"]["id"]

    first = client.get("/api/v1/worlds/" + world_id + "/export")
    assert first.headers["X-World-Copy-Count"] == "1"
    # 同一天再取一次：明细再记一条，但计数不累加。
    second = client.get("/api/v1/worlds/" + world_id + "/export")
    assert second.headers["X-World-Copy-Count"] == "1"

    with app.app_context():
        assert WorldCopyStat.query.filter_by(world_id=world_id).count() == 2


def test_export_requires_login(app, client):
    _login(app, client)
    world_id = _publish(
        client, json.dumps(_payload(), ensure_ascii=False)
    ).get_json()["data"]["id"]
    client.get("/auth/logout")
    assert client.get("/api/v1/worlds/" + world_id + "/export").status_code == 401


def test_delete_hides_it(app, client):
    _login(app, client)
    world_id = _publish(
        client, json.dumps(_payload(), ensure_ascii=False)
    ).get_json()["data"]["id"]

    assert client.delete("/api/v1/worlds/" + world_id).get_json()["ok"] is True
    with app.app_context():
        world = db.session.get(WorldCard, world_id)
        assert world.is_hidden is True
        assert world.status == "deleted"
