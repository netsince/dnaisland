"""生图历史列表：**过滤必须在 SQL 里做完，再分页**。

真实事故：社区用户「辞安」有 453 条生图记录（失败 189 条），其中最新 23 条是连续失败。
他想翻自己的生图记录，界面上却写着「还没有生图记录」——因为列表接口先 `paginate()` 取 12 行
原始记录，再在 Python 里把没有产出图的行逐条 `continue` 掉：最新 12 条恰好全是失败记录，
于是 `items` 是空列表；而 `total`/`pages` 又按全部记录算（453 条 / 38 页），自相矛盾。

网页端和 App 端是两个入口、同一份逻辑，所以两边都在这里守住：

* 默认（不要失败记录）：第 1 页必须是「有产出图」的最新一页，total 只数这些记录；
* `?include_failed=1`：失败记录也回来（App 详情页/历史要显示失败原因），总数把失败算进去；
* 翻页既不重复也不漏行（created_at 同秒时要靠 id 兜底排序）。
"""

import json
from datetime import datetime, timedelta

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

ONE_IMAGE = json.dumps(
    ["data:image/webp;base64,UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuUAAA="]
)
BASE_TIME = datetime(2026, 11, 19, 5, 0, 0)


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


def _user(username="ig_page_user", nickname="分页用户"):
    u = User(username=username, nickname=nickname, email=f"{username}@example.com")
    u.set_password("pass123")
    db.session.add(u)
    db.session.commit()
    return u


def _model():
    m = GenerationModel(
        name="gpt-image-1", display_name="G Imagine", points_per_image=1, enabled=True
    )
    db.session.add(m)
    db.session.commit()
    return m


def _add(user, model, *, status, created_at, prompt):
    row = GenerationLog(
        user_id=user.id,
        model_id=model.id,
        model_name=model.display_name,
        prompt=prompt,
        size="1024x1024",
        count=1,
        references_count=0,
        status=status,
        images="[]" if status == "failed" else ONE_IMAGE,
        points_spent=0 if status == "failed" else 1,
        error="生图接口错误 (400)" if status == "failed" else None,
        created_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _seed_rerun_of_incident(user, model, successes=2, failures=14):
    """复刻事故现场：成功的图在更早，之后连续一堆失败（最新 failures 条全是失败）。"""
    for i in range(successes):
        _add(
            user,
            model,
            status="success",
            created_at=BASE_TIME + timedelta(minutes=i),
            prompt=f"成功的图{i + 1}",
        )
    for i in range(failures):
        _add(
            user,
            model,
            status="failed",
            created_at=BASE_TIME + timedelta(minutes=10 + i),
            prompt=f"失败{i + 1}",
        )


def _login_web(client, identifier="ig_page_user"):
    r = client.post("/auth/login", data={"identifier": identifier, "password": "pass123"})
    assert r.status_code in (200, 302), r.status_code


def _app_token(client, identifier="ig_page_user"):
    r = client.post(
        "/api/v1/auth/token", json={"identifier": identifier, "password": "pass123"}
    )
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["token"]


def test_web_first_page_is_not_emptied_by_newest_failed_records(app, client):
    """网页端：最新 12 条全是失败记录时，第 1 页必须给出真正有图的记录。"""
    with app.app_context():
        u = _user()
        m = _model()
        _seed_rerun_of_incident(u, m)
    _login_web(client)

    res = client.get("/image-gen/api/logs").get_json()
    assert res["ok"] is True
    assert [it["prompt"] for it in res["items"]] == ["成功的图2", "成功的图1"], (
        "默认第 1 页被失败记录占满后整页变空：这正是用户看到的「还没有生图记录」"
    )
    assert res["total"] == 2, "total 必须只数有产出图的记录"
    assert res["pages"] == 1
    assert res["has_next"] is False


def test_app_first_page_is_not_emptied_by_newest_failed_records(app, client):
    """App 端：同一个坑，同一个答案。"""
    with app.app_context():
        u = _user()
        m = _model()
        _seed_rerun_of_incident(u, m)
    token = _app_token(client)

    res = client.get(
        "/api/v1/image-gen/logs", headers={"Authorization": f"Bearer {token}"}
    ).get_json()["data"]
    assert [it["prompt"] for it in res["items"]] == ["成功的图2", "成功的图1"]
    assert res["total"] == 2
    assert res["pages"] == 1


def test_include_failed_still_returns_failed_records_in_order(app, client):
    """显式要失败记录时（App 详情/历史用），失败记录照旧出现在正确的位置。"""
    with app.app_context():
        u = _user()
        m = _model()
        _seed_rerun_of_incident(u, m)
    token = _app_token(client)

    res = client.get(
        "/api/v1/image-gen/logs?include_failed=1",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()["data"]
    assert res["total"] == 16, "带 include_failed 时总数要把失败记录算进去"
    assert len(res["items"]) == 12, "一页 12 条：带 include_failed 就该是满的"
    assert res["items"][0]["prompt"] == "失败14", "最新的记录排最前"
    assert res["items"][0]["status"] == "failed"
    assert res["items"][0]["images"] == [] and res["items"][0]["first_image"] == ""


def test_paging_covers_every_visible_record_exactly_once(app, client):
    """created_at 相同（同秒批量插入）时翻页既不重复也不漏。"""
    with app.app_context():
        u = _user()
        m = _model()
        for i in range(30):
            _add(
                u,
                m,
                status="success",
                created_at=BASE_TIME,
                prompt=f"同一秒的图{i + 1}",
            )
        for i in range(5):
            _add(
                u,
                m,
                status="failed",
                created_at=BASE_TIME + timedelta(minutes=1),
                prompt=f"失败{i + 1}",
            )
    _login_web(client)

    seen = []
    page = 1
    while True:
        res = client.get(f"/image-gen/api/logs?page={page}&per_page=10").get_json()
        seen.extend(it["id"] for it in res["items"])
        if not res["has_next"]:
            break
        page += 1
        assert page <= 10, "翻页没有收敛，说明分页信息算错了"
    assert len(seen) == 30
    assert len(set(seen)) == 30, "翻页出现了重复记录"
