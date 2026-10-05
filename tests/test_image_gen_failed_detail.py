"""失败记录的**详情页**必须显示完整信息（admin 后台的「查看」也走这个页面）。

用户反馈：admin 后台查看失败的生图任务时，**只能看到那句错误**，看不到用户的提示词、
参数以及提交的参考图。

原因：admin 列表的「查看」跳的是 `/image-gen/logs/<id>`，而该模板把**整个右侧信息栏**
包在 `{% if imgs %}` 里 —— 失败记录没有产出图，于是只剩一句「本次未产出图像。」+ 错误。
数据其实一直都在：`_fail_task` 存了 prompt / size / count / references_count /
reference_images / model_name / error。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

# 一张极小的 WebP data URL（内容不重要，只验证"参考图有没有被展示出来"）。
REF_DATA_URL = "data:image/webp;base64,UklGRjoAAABXRUJQVlA4IC4AAAAQAgCdASoIAAgAAUAmJaACdLoB+AH4AAPIAP7tTd/9QWCsy/ew/+jBcBwPeEAA"


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


def _seed_failed_log():
    u = User(username="ig_fail_user", nickname="失败用户", email="ig_fail@example.com")
    u.set_password("pass123")
    db.session.add(u)
    m = GenerationModel(
        name="gpt-image-1", display_name="G Imagine", points_per_image=1, enabled=True
    )
    db.session.add(m)
    db.session.commit()

    bad = GenerationLog(
        user_id=u.id,
        model_id=m.id,
        model_name=m.display_name,
        prompt="一只戴帽子的猫，赛博朋克风格",
        size="768x1024",
        count=2,
        references_count=2,
        status="failed",
        images="[]",
        reference_images=json.dumps([REF_DATA_URL, REF_DATA_URL]),
        points_spent=0,
        error="生图接口错误 (400): Invalid request: multipart: NextPart: EOF",
    )
    db.session.add(bad)
    db.session.commit()
    return bad.id


def _login(client, identifier="ig_fail_user", password="pass123"):
    r = client.post("/auth/login", data={"identifier": identifier, "password": password})
    assert r.status_code in (200, 302), r.status_code


def test_failed_detail_shows_prompt_params_and_references(app, client):
    """失败记录的详情页必须有提示词/模型/尺寸/张数/参考图/原因（admin 也看这个页面）。

    这条在修之前会红：旧模板在无图分支里只渲染「本次未产出图像。」+ 错误，
    提示词与参数整块不出现。
    """
    with app.app_context():
        bad_id = _seed_failed_log()
    _login(client)

    html = client.get(f"/image-gen/logs/{bad_id}").get_data(as_text=True)

    assert "一只戴帽子的猫，赛博朋克风格" in html, "失败记录必须显示提示词"
    assert "提示词 Prompt" in html, "信息栏（提示词块）必须在失败时也渲染"
    assert "G Imagine" in html, "必须显示模型"
    assert "768x1024" in html, "必须显示尺寸"
    assert "张数：2 张" in html, "必须显示张数"
    assert "消耗：0 点" in html, "必须显示消耗（失败不扣费，所以是 0）"
    assert "multipart: NextPart: EOF" in html, "必须显示失败原因"
    # 参考图：列表用 /image-gen/reference/<id>/<idx> 端点按序号取
    assert f"/image-gen/reference/{bad_id}/0" in html, "必须显示所用参考图"
    assert f"/image-gen/reference/{bad_id}/1" in html
    assert "所用参考图（2 张）" in html
    # 没有产出图时不给"下载原图"（点了也没用），但给占位说明。
    # 注意别断言 js-dlpng 这个类名：页面脚本里本来就会提到它。
    assert "本次未产出图像" in html
    assert "下载原图" not in html, "没有产出图就不该出现下载按钮"
