"""网页版发布与编辑（/worlds/publish、/worlds/<id>/edit）。

P1 之后网页端不再是「贴 JSON 的文本框」，而是**完整世界观编辑器**：表单字段 +
词条列表（含正则触发/递归/冷却/延迟/装饰符），提交时序列化成 entries_json，
服务端拼回客户端认识的 World JSON。

这里守四件事：
* 表单能发、能编辑，两端口径一致（共用 publish_world / update_world）；
* 词条的高级规则能原样进 payload（否则编辑一次就丢一半信息）；
* 顶层必须带 id —— 客户端 World.fromJson 把 id 当非空字符串读；
* 编辑只允许作者本人。
"""

import json
import re

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard, WorldTag
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login(app, client, username="wp"):
    with app.app_context():
        if User.query.filter_by(username=username).first() is None:
            u = User(username=username, nickname=username, email=username + "@example.com")
            u.set_password("pass123")
            db.session.add(u)
            db.session.commit()
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


def _entries(*items):
    return json.dumps(list(items), ensure_ascii=False)


def io_bytes(text):
    import io

    return io.BytesIO(text.encode("utf-8"))


def _flashes(html):
    """从页面里取出 flash 消息（base.html 用 tojson 注入脚本）。"""
    m = re.search(r"var msgs = (\[.*?\]);", html, re.S)
    if not m:
        return []
    return [item[1] for item in json.loads(m.group(1))]


def test_requires_login(app, client):
    assert client.get("/worlds/publish").status_code in (302, 401)


def test_form_renders_editor_fields(app, client):
    _login(app, client)
    html = client.get("/worlds/publish").get_data(as_text=True)
    assert "发布世界卡" in html
    # 完整编辑器的标志：名称 / 标签 / 词条序列化字段 / 添加词条按钮
    assert 'name="name"' in html
    assert 'name="tags"' in html
    assert 'id="entriesJson"' in html
    assert "添加词条" in html
    # 还有「从 JSON / .dnaworld 导入」的面板（不想手打就贴进来）
    assert 'value="import"' in html
    assert 'name="file"' in html


def test_publish_from_form(app, client):
    _login(app, client)
    resp = client.post(
        "/worlds/publish",
        data={
            "name": "幻想大陆",
            "summary": "一个世界",
            "description": "背景",
            "tags": "奇幻, 冒险",
            "forbidden_words": "禁词",
            "entries_json": _entries(
                {"id": "e1", "name": "王都", "description": "首都", "type": "noun"},
                {"id": "e2", "name": "艾琳", "description": "女王", "type": "person"},
            ),
        },
    )
    assert resp.status_code in (200, 302)
    with app.app_context():
        world = WorldCard.query.filter_by(name="幻想大陆").first()
        assert world is not None
        assert world.status == "pending"
        assert world.entry_count == 2
        # 词条名快照：列表卡片要用，不解析 payload
        assert world.preview_list == ["王都", "艾琳"]
        assert sorted(t.tag for t in WorldTag.query.filter_by(world_id=world.id)) == [
            "冒险",
            "奇幻",
        ]
        # 顶层必须带 id，否则客户端导入会崩
        data = json.loads(world.payload)
        assert isinstance(data.get("id"), str) and data["id"]


def test_entry_advanced_fields_round_trip(app, client):
    """高级规则必须原样进 payload —— 编辑一次丢一半信息，编辑器就白做了。"""
    _login(app, client)
    client.post(
        "/worlds/publish",
        data={
            "name": "规则世界",
            "entries_json": _entries(
                {
                    "id": "e1",
                    "name": "王都",
                    "description": "首都",
                    "type": "noun",
                    "keys": ["都城", "首都"],
                    "keyRegex": "^王.*$",
                    "caseSensitive": True,
                    "matchWholeWords": True,
                    "recursive": True,
                    "order": 3,
                    "cooldownRounds": 2,
                    "delayRounds": 1,
                    "decorator": "activate",
                }
            ),
        },
    )
    with app.app_context():
        world = WorldCard.query.filter_by(name="规则世界").first()
        assert world is not None
        entry = json.loads(world.payload)["entries"][0]
        assert entry["keys"] == ["都城", "首都"]
        assert entry["keyRegex"] == "^王.*$"
        assert entry["caseSensitive"] is True
        assert entry["matchWholeWords"] is True
        assert entry["recursive"] is True
        assert entry["order"] == 3
        assert entry["cooldownRounds"] == 2
        assert entry["delayRounds"] == 1
        assert entry["decorator"] == "activate"


def test_bad_entries_json_degrades_to_empty(app, client):
    """entries_json 坏掉不该 500：当成空词条列表，世界本身照发。"""
    _login(app, client)
    client.post(
        "/worlds/publish",
        data={"name": "坏词条", "entries_json": "not json"},
    )
    with app.app_context():
        world = WorldCard.query.filter_by(name="坏词条").first()
        assert world is not None
        assert world.entry_count == 0


def test_missing_name_shows_error(app, client):
    _login(app, client)
    html = client.post(
        "/worlds/publish", data={"name": "", "entries_json": "[]"}
    ).get_data(as_text=True)
    assert any("名称" in (msg or "") for msg in _flashes(html))
    with app.app_context():
        assert WorldCard.query.count() == 0


def test_duplicate_is_idempotent(app, client):
    _login(app, client)
    form = {"name": "幻想大陆", "entries_json": "[]"}
    client.post("/worlds/publish", data=form)
    client.post("/worlds/publish", data=form)
    with app.app_context():
        assert WorldCard.query.filter_by(name="幻想大陆").count() == 1


def _seed_world(app, author_username="wp"):
    with app.app_context():
        author = User.query.filter_by(username=author_username).first()
        raw = json.dumps(
            {
                "id": "local-1",
                "name": "旧世界",
                "summary": "旧简介",
                "tags": ["旧标签"],
                "entries": [
                    {"id": "e1", "name": "旧词条", "description": "旧正文", "type": "noun"}
                ],
            },
            ensure_ascii=False,
        )
        world = WorldCard(
            id="world-edit",
            author_id=author.id,
            name="旧世界",
            summary="旧简介",
            entry_count=1,
            payload_bytes=len(raw.encode("utf-8")),
            payload=raw,
            status="approved",
        )
        db.session.add(world)
        db.session.add(WorldTag(world_id="world-edit", tag="旧标签"))
        db.session.commit()
        return world.id


def test_edit_form_is_prefilled(app, client):
    _login(app, client)
    world_id = _seed_world(app)
    html = client.get(f"/worlds/{world_id}/edit").get_data(as_text=True)
    assert "编辑世界卡" in html
    assert "旧世界" in html
    # 词条是喂给 JS 的（Jinja 的 tojson 会把中文转成 \uXXXX），所以按转义后的串断言。
    assert json.dumps("旧词条")[1:-1] in html
    assert '"e1"' in html


def test_edit_replaces_payload_and_requeues(app, client):
    _login(app, client)
    world_id = _seed_world(app)
    resp = client.post(
        f"/worlds/{world_id}/edit",
        data={
            "world_id": world_id,
            "name": "新世界",
            "summary": "新简介",
            "tags": "新标签",
            "entries_json": _entries(
                {"id": "e9", "name": "新词条", "description": "新正文", "type": "person"}
            ),
        },
    )
    assert resp.status_code in (200, 302)
    with app.app_context():
        world = db.session.get(WorldCard, world_id)
        assert world.name == "新世界"
        # 替换后必须回到待审核：审核过的是旧内容
        assert world.status == "pending"
        assert world.entry_count == 1
        assert world.preview_list == ["新词条"]
        assert [t.tag for t in WorldTag.query.filter_by(world_id=world_id)] == ["新标签"]
        assert json.loads(world.payload)["name"] == "新世界"


def test_edit_requires_ownership(app, client):
    _login(app, client)
    world_id = _seed_world(app)
    client.get("/auth/logout")
    _login(app, client, username="other")
    assert client.get(f"/worlds/{world_id}/edit").status_code == 404


# ---------------------------------------------------------------------------
# 导入：粘贴 JSON / 上传 .dnaworld，填进表单（不直接发布）
# ---------------------------------------------------------------------------


def _escaped(text):
    """Jinja 的 tojson 会把中文转成 \\uXXXX，按转义后的串断言。"""
    return json.dumps(text)[1:-1]


def test_import_bare_world_json_fills_form(app, client):
    _login(app, client)
    payload = json.dumps(
        {
            "name": "导入的世界",
            "summary": "简介",
            "tags": ["奇幻"],
            "entries": [
                {"id": "e1", "name": "王都", "description": "首都", "type": "noun"}
            ],
        },
        ensure_ascii=False,
    )
    html = client.post(
        "/worlds/publish", data={"do": "import", "payload": payload}
    ).get_data(as_text=True)

    assert "导入的世界" in html
    assert _escaped("王都") in html
    # 只填表，不发布
    with app.app_context():
        assert WorldCard.query.count() == 0


def test_editor_person_block_visible_by_default(app, client):
    """人物字段块不能默认 d-none。

    之前是「默认藏起来，等 JS 按类型展开」——脚本一旦没跑到（或类型判断出错），
    字段就被藏住，用户以为没有这几项，保存后性别/年龄/状态/关联全空。
    现在默认可见，JS 只负责把「名词」词条的那块藏起来。
    """
    _login(app, client)
    html = client.get("/worlds/publish").get_data(as_text=True)
    m = re.search(r'<div class="([^"]*)" data-person>', html)
    assert m is not None, "模板里应有 data-person 块"
    assert "d-none" not in m.group(1)


def test_editor_shows_hot_tags(app, client):
    """网页编辑器也给热门标签候选（社区侧才该有这东西）。"""
    _login(app, client)
    _seed_world(app)  # 带标签「旧标签」的世界卡
    html = client.get("/worlds/publish").get_data(as_text=True)
    assert "热门标签" in html
    assert 'data-tag="旧标签"' in html


def test_editor_entries_are_collapsible(app, client):
    """词条默认收起、点标题展开 —— 几百条的世界书全展开会拉成长龙。"""
    _login(app, client)
    html = client.get("/worlds/publish").get_data(as_text=True)
    assert "dna-entry__summary" in html
    assert "dna-entry__details" in html


def test_export_returns_dnaworld_attachment(app, client):
    """编辑器可以直接导出当前表单（.dnaworld 信封），且不写库。"""
    _login(app, client)
    resp = client.post(
        "/worlds/publish",
        data={
            "do": "export",
            "name": "导出世界",
            "summary": "简介",
            "tags": "奇幻",
            "entries_json": _entries(
                {"id": "e1", "name": "王都", "description": "首都", "type": "noun"}
            ),
        },
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    body = json.loads(resp.get_data(as_text=True))
    assert body["exportType"] == "world"
    assert body["world"]["name"] == "导出世界"
    assert body["world"]["entries"][0]["name"] == "王都"
    with app.app_context():
        assert WorldCard.query.count() == 0


def test_export_validates_first(app, client):
    """没名字就导出？先拦住 —— 导出一份没有名字的世界没有意义。"""
    _login(app, client)
    html = client.post(
        "/worlds/publish", data={"do": "export", "name": "", "entries_json": "[]"}
    ).get_data(as_text=True)
    assert any("名称" in (msg or "") for msg in _flashes(html))


def test_person_fields_round_trip(app, client):
    """人物的性别/年龄/状态/关联必须能存进去。

    网页编辑器早先没有这四个字段，结果是「在网页上编辑一次人物，属性就被清空」。
    """
    _login(app, client)
    client.post(
        "/worlds/publish",
        data={
            "name": "人物世界",
            "entries_json": _entries(
                {
                    "id": "p1",
                    "name": "艾琳",
                    "description": "女王",
                    "type": "person",
                    "gender": "female",
                    "age": "24",
                    "status": "dead",
                    "relationTarget": "p2",
                    "relationContent": "宿敌",
                },
                {
                    "id": "p2",
                    "name": "王都",
                    "description": "首都",
                    "type": "noun",
                    # 名词上的这些字段应该被丢掉
                    "gender": "female",
                    "age": "99",
                },
            ),
        },
    )
    with app.app_context():
        world = WorldCard.query.filter_by(name="人物世界").first()
        assert world is not None
        entries = json.loads(world.payload)["entries"]
        person = entries[0]
        assert person["gender"] == "female"
        assert person["age"] == "24"
        assert person["status"] == "dead"
        assert person["relation"] == {"targetId": "p2", "content": "宿敌"}
        noun = entries[1]
        assert noun["gender"] is None
        assert noun["age"] is None
        assert noun["status"] is None
        assert noun["relation"] is None


def test_entry_without_name_is_rejected(app, client):
    """有正文却没名字的词条要拦住（与本体一致）。

    放过去的话，这条词条既不会出现在详情页目录里，也永远触发不了（没有主触发词），
    属于静默坏数据。
    """
    _login(app, client)
    html = client.post(
        "/worlds/publish",
        data={
            "name": "坏词条世界",
            "entries_json": _entries(
                {"id": "e1", "name": "", "description": "有正文但没名字"}
            ),
        },
    ).get_data(as_text=True)

    assert any("没有名称" in (msg or "") for msg in _flashes(html))
    with app.app_context():
        assert WorldCard.query.count() == 0


def test_rounds_are_clamped(app, client):
    """冷却/延迟轮数与本体一致收在 0–100，避免手滑输入把词条弄成永不触发。"""
    _login(app, client)
    client.post(
        "/worlds/publish",
        data={
            "name": "轮数世界",
            "entries_json": _entries(
                {"id": "e1", "name": "王都", "description": "x",
                 "cooldownRounds": 999, "delayRounds": -5}
            ),
        },
    )
    with app.app_context():
        entry = json.loads(
            WorldCard.query.filter_by(name="轮数世界").first().payload
        )["entries"][0]
        assert entry["cooldownRounds"] == 100
        assert entry["delayRounds"] == 0


def test_import_keeps_person_fields(app, client):
    """导入 .dnaworld 时，人物的性别/年龄/状态/关联要进编辑器。"""
    _login(app, client)
    payload = json.dumps(
        {
            "name": "带人物的世界",
            "entries": [
                {
                    "id": "p1",
                    "name": "艾琳",
                    "type": "person",
                    "gender": "female",
                    "age": "24",
                    "status": "dead",
                    "relation": {"targetId": "p2", "content": "宿敌"},
                },
                {"id": "p2", "name": "王都", "type": "noun"},
            ],
        },
        ensure_ascii=False,
    )
    html = client.post(
        "/worlds/publish", data={"do": "import", "payload": payload}
    ).get_data(as_text=True)
    assert _escaped("female") in html
    assert _escaped("dead") in html
    assert _escaped("宿敌") in html


def test_import_envelope_format(app, client):
    """本应用导出的 .dnaworld 是 {exportType, world: {...}} 信封。"""
    _login(app, client)
    envelope = json.dumps(
        {
            "version": 1,
            "exportType": "world",
            "world": {
                "name": "信封世界",
                "entries": [{"id": "e1", "name": "词条A", "keys": ["甲"]}],
            },
        },
        ensure_ascii=False,
    )
    html = client.post(
        "/worlds/publish", data={"do": "import", "payload": envelope}
    ).get_data(as_text=True)
    assert "信封世界" in html
    assert _escaped("词条A") in html
    assert _escaped("甲") in html


def test_import_third_party_worldbook(app, client):
    """第三方世界书：entries 是以 uid 为键的对象，用 key/content/disable。"""
    _login(app, client)
    worldbook = json.dumps(
        {
            "name": "酒馆世界书",
            "entries": {
                "0": {"key": "王都,都城", "content": "首都", "order": 1, "constant": True},
                "1": {"key": "禁用词条", "content": "不该出现", "disable": True},
            },
        },
        ensure_ascii=False,
    )
    html = client.post(
        "/worlds/publish", data={"do": "import", "payload": worldbook}
    ).get_data(as_text=True)
    assert "酒馆世界书" in html
    # key 的第一个词做名字，其余做触发词
    assert _escaped("王都") in html
    assert _escaped("都城") in html
    # disable 的条目要丢掉
    assert _escaped("禁用词条") not in html


def test_import_from_uploaded_file(app, client):
    _login(app, client)
    raw = json.dumps(
        {"name": "文件世界", "entries": [{"id": "e1", "name": "词条B"}]},
        ensure_ascii=False,
    )
    html = client.post(
        "/worlds/publish",
        data={"do": "import", "file": (io_bytes(raw), "x.dnaworld")},
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "文件世界" in html
    assert _escaped("词条B") in html


def test_import_bad_json_shows_error(app, client):
    _login(app, client)
    html = client.post(
        "/worlds/publish", data={"do": "import", "payload": "not json"}
    ).get_data(as_text=True)
    assert any("合法的 JSON" in (msg or "") for msg in _flashes(html))
    with app.app_context():
        assert WorldCard.query.count() == 0
