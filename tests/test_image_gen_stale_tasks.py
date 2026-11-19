"""进行中的生图任务必须能被收敛掉，否则一条死任务会把用户永久锁死。

事故：2026-11-19 18:18 一位用户提交了生图，任务落库为 pending、线程把它改成
processing，然后 gunicorn 在 18:46 重启 —— 线程没了，行还写着 processing。三个半
小时后他再点生成，只得到「已有进行中的生图任务，请稍候」，永远等不到。

两个缺口叠在一起：

1. `recover_stale_tasks` 只扫 `processing`，`pending` 永远没人管：进程如果在
   「提交事务」和「线程认领」之间断掉，那行 pending 不会被任何人认领，也不会被任何
   清理逻辑碰一下；
2. 收敛只在「任务列表」接口里发生。而客户端只有在自己已知有任务时才轮询列表 ——
   没任务的人永远不请求那个接口，于是永远收敛不了。

这里把两条都钉住：两个状态都要扫（阈值不同），并且 `/generate` 判断
「有没有进行中的任务」之前必须自己先收敛一遍。
"""

from datetime import datetime, timedelta

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationModel, GenerationTask
from app.models.user import User
from app.services import generation_worker
from app.services.generation_worker import recover_stale_tasks
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


@pytest.fixture
def no_worker(monkeypatch):
    """真线程会去打 mock.example.com：拦掉，测试只关心任务状态。"""
    from app.routes import api as api_routes
    from app.routes import image_gen as image_gen_routes

    monkeypatch.setattr(image_gen_routes, "process_generation_task", lambda app, task_id: None)
    monkeypatch.setattr(api_routes, "process_generation_task", lambda app, task_id: None)


def _seed(points=100):
    u = User(username="stale_user", nickname="卡任务用户", email="stale@example.com")
    u.set_password("pass123")
    u.points = points
    db.session.add(u)
    m = GenerationModel(
        name="gpt-image-1",
        display_name="测试模型",
        points_per_image=5,
        enabled=True,
        api_base_url="https://mock.example.com/v1",
        api_key="sk-mock",
    )
    db.session.add(m)
    db.session.commit()
    return u, m


def _add_task(user, model, *, status, minutes_ago, prompt="卡住的那条"):
    when = datetime.utcnow() - timedelta(minutes=minutes_ago)
    task = GenerationTask(
        user_id=user.id,
        model_id=model.id,
        model_name=model.display_name,
        prompt=prompt,
        size="1024x1024",
        count=1,
        references_count=0,
        status=status,
        created_at=when,
        updated_at=when,
    )
    db.session.add(task)
    db.session.commit()
    return task.id


def _login_web(client, identifier="stale_user"):
    r = client.post("/auth/login", data={"identifier": identifier, "password": "pass123"})
    assert r.status_code in (200, 302), r.status_code


def _app_token(client, identifier="stale_user"):
    r = client.post("/api/v1/auth/token", json={"identifier": identifier, "password": "pass123"})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["token"]


def _status_of(task_id):
    db.session.expire_all()
    return db.session.get(GenerationTask, task_id).status


def _generate_web(client, model_id):
    return client.post(
        "/image-gen/generate",
        data={"model": model_id, "prompt": "一只猫", "size": "1:1", "count": 1},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


def test_stale_pending_task_is_closed_and_web_generate_proceeds(app, client, no_worker):
    """进程死在「提交事务」和「线程认领」之间的那种任务：不许挡住用户。"""
    with app.app_context():
        u, m = _seed()
        stale_id = _add_task(u, m, status="pending", minutes_ago=30, prompt="卡在 pending 的")
        model_id = m.id
    _login_web(client)

    r = _generate_web(client, model_id)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True, "卡住的 pending 任务不该再挡住新的生成"

    with app.app_context():
        assert _status_of(stale_id) == "failed", "死任务要被收敛成 failed，而不是留着假装在跑"
        assert GenerationTask.query.filter_by(status="pending").count() == 1, "新任务照常落库"


def test_stale_processing_task_is_closed_when_listing(app, client):
    """轮询列表也要能收敛：这里是唯一会被客户端周期性调用的地方。"""
    with app.app_context():
        u, m = _seed()
        stale_id = _add_task(u, m, status="processing", minutes_ago=200)
    _login_web(client)

    res = client.get("/image-gen/api/tasks").get_json()
    assert res["tasks"] == [], "超时的 processing 任务不该再出现在进行中列表里"

    with app.app_context():
        assert _status_of(stale_id) == "failed"
        task = db.session.get(GenerationTask, stale_id)
        assert task.error, "收敛时要有失败原因，不能是一句空白"


def test_fresh_processing_task_still_blocks(app, client, no_worker):
    """真的在跑的任务当然要挡住第二次提交（否则并发扣点）。"""
    with app.app_context():
        u, m = _seed()
        running_id = _add_task(u, m, status="processing", minutes_ago=1)
        model_id = m.id
    _login_web(client)

    r = _generate_web(client, model_id)
    assert r.status_code == 400
    assert "已有进行中的生图任务" in r.get_json()["error"]

    with app.app_context():
        assert _status_of(running_id) == "processing", "没过阈值的任务不许被动"


def test_thresholds_pending_shorter_than_processing(app):
    """pending 正常只活几毫秒，阈值必须比 processing 短得多。"""
    with app.app_context():
        u, m = _seed()
        pending_id = _add_task(u, m, status="pending", minutes_ago=10)
        processing_id = _add_task(u, m, status="processing", minutes_ago=10)

        closed = recover_stale_tasks()
        assert closed == 1, "10 分钟前的 pending 已经死了，10 分钟前的 processing 可能还在跑"
        assert _status_of(pending_id) == "failed"
        assert _status_of(processing_id) == "processing"


def test_stale_pending_does_not_block_app_generate(app, client, no_worker):
    """App 端同一个坑、同一个答案。"""
    with app.app_context():
        u, m = _seed()
        stale_id = _add_task(u, m, status="pending", minutes_ago=30)
        model_id = m.id
    token = _app_token(client)

    r = client.post(
        "/api/v1/image-gen/generate",
        json={"prompt": "一只猫", "model_id": model_id, "size": "1:1", "count": 1, "references": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True

    with app.app_context():
        assert _status_of(stale_id) == "failed"


def test_app_task_list_closes_stale_processing(app, client):
    with app.app_context():
        u, m = _seed()
        stale_id = _add_task(u, m, status="processing", minutes_ago=200)
    token = _app_token(client)

    res = client.get(
        "/api/v1/image-gen/tasks", headers={"Authorization": f"Bearer {token}"}
    ).get_json()["data"]
    assert res["tasks"] == []

    with app.app_context():
        assert _status_of(stale_id) == "failed"
