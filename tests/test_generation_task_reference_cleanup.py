"""任务表上的参考图是**中转数据**，进入终态后必须清空。

背景：库里实测 generation_tasks.reference_data 占了 843MB，而它只在
``_build_reference_files`` 里被读一次（后台线程喂给模型之前）。任务一旦
succeeded / failed / 超时回收，这份数据就没有任何读取方 —— 但历史上从来
没有清过，于是和 GenerationLog.reference_images 长期各存一份，逐字节相同。

这组测试钉住三件事：
1. 成功任务落库后 reference_data 为 None；
2. 失败任务同样为 None（含模型不存在这种"还没读参考图就失败"的分支）；
3. 超时回收的任务也为 None。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel, GenerationTask
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

REF_DATA_URL = (
    "data:image/webp;base64,UklGRjoAAABXRUJQVlA4IC4AAAAQAgCdASoIAAgAAUAmJaACdLoB"
    "+AH4AAPIAP7tTd/9QWCsy/ew/+jBcBwPeEAA"
)


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


def _seed_task(reference_data):
    u = User(username="worker_user", nickname="任务用户", email="worker@example.com")
    u.set_password("pass123")
    db.session.add(u)
    m = GenerationModel(
        name="gpt-image-1", display_name="G Imagine", points_per_image=1, enabled=True
    )
    db.session.add(m)
    db.session.commit()

    task = GenerationTask(
        user_id=u.id,
        model_id=m.id,
        model_name=m.display_name,
        prompt="一只猫",
        size="1024x1024",
        count=1,
        references_count=1,
        reference_data=reference_data,
        status="processing",
    )
    db.session.add(task)
    db.session.commit()
    return task, m


def _ref_payload():
    return json.dumps(
        [{"filename": "a.webp", "mimetype": "image/webp", "data_b64": REF_DATA_URL}],
        ensure_ascii=False,
    )


def test_成功任务落库后清空参考图(app):
    """走真实的成功收尾函数，而不是手抄一段等价代码。"""
    from app.services.generation_worker import _finish_task_success

    task, model = _seed_task(_ref_payload())
    user = db.session.get(User, task.user_id)

    log = _finish_task_success(task, model, user, ["data:image/png;base64,AAAA"])

    fresh = db.session.get(GenerationTask, task.id)
    assert fresh.status == "succeeded"
    assert fresh.reference_data is None, "中转数据必须清空"
    # 日志那一份必须还在 —— 用户回看要用
    log_fresh = db.session.get(GenerationLog, log.id)
    assert log_fresh.reference_images == _ref_payload()


def test_失败任务清空参考图(app):
    from app.services.generation_worker import _fail_task

    task, model = _seed_task(_ref_payload())
    _fail_task(task, "上游超时", model)

    fresh = db.session.get(GenerationTask, task.id)
    assert fresh.status == "failed"
    assert fresh.reference_data is None, "失败任务的中转数据同样没有读取方"

    log = db.session.get(GenerationLog, fresh.result_log_id)
    assert log is not None
    assert log.reference_images == _ref_payload(), "失败日志仍要保留参考图供回看"


def test_模型不存在导致失败时也不残留(app):
    from app.services.generation_worker import _fail_task

    task, _ = _seed_task(_ref_payload())
    # model=None 是「模型已被删除」的分支：此时还没走到读参考图那一步
    _fail_task(task, "生图模型不存在，任务已终止", None)

    fresh = db.session.get(GenerationTask, task.id)
    assert fresh.status == "failed"
    assert fresh.reference_data is None


def test_超时回收也清空参考图(app):
    from datetime import datetime, timedelta

    from app.services.generation_worker import recover_stale_tasks

    task, _ = _seed_task(_ref_payload())
    # 手动把它推到「很久没更新」
    task.updated_at = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()

    recover_stale_tasks(app)

    fresh = db.session.get(GenerationTask, task.id)
    assert fresh.status == "failed"
    assert fresh.reference_data is None


def test_没有参考图时不再写空数组(app):
    """写入侧：ref_payload 为空时应写 None，而不是字符串 "[]"。

    否则「有没有参考图」这个判断永远为真，清理脚本也没法按 NULL 区分。
    """
    from app.services.generation_worker import _fail_task

    task, model = _seed_task(None)
    _fail_task(task, "x", model)

    log = db.session.get(GenerationLog, task.result_log_id)
    assert log.reference_images is None
    assert log.images is None
