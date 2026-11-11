"""生图产物是**公开资源**：不要鉴权、可被 CDN 缓存。

背景：线上 Cloudflare 一直在 `cf-cache-status: DYNAMIC`，图片全量回源，
单张 135KB 的图 TTFB 1~12 秒。根因是每个响应都带 `Vary: Cookie`
（before_request 里访问了 current_user），Cloudflare 据此判定响应随
登录态变化，直接放弃缓存。

这组测试钉住三件事：
1. 未登录也能取到图片（公开，不再 403）；
2. 响应带 `Cache-Control: public, max-age=...`；
3. `Vary` 里**不能**出现 Cookie（否则 CDN 又不缓存了）。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

# 1x1 的极小 PNG data URL
IMG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    # 与线上保持一致（线上静态资源返回 max-age=14400）。
    # 不设的话 Flask 在 TESTING 下默认 no-cache，测不出 public 升级逻辑。
    SEND_FILE_MAX_AGE_DEFAULT = 14400


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_log(app):
    u = User(username="pubimg", nickname="公开图", email="pubimg@example.com")
    u.set_password("pass123")
    db.session.add(u)
    m = GenerationModel(name="gpt-image-1", display_name="G", points_per_image=1, enabled=True)
    db.session.add(m)
    db.session.commit()
    log = GenerationLog(
        user_id=u.id,
        model_id=m.id,
        model_name="G",
        prompt="一只猫",
        size="1024x1024",
        count=1,
        references_count=1,
        status="success",
        images=json.dumps([IMG_DATA_URL]),
        reference_images=json.dumps(
            [{"filename": "a.png", "mimetype": "image/png",
              "data_b64": IMG_DATA_URL.split(",", 1)[1]}]
        ),
        points_spent=0,
    )
    db.session.add(log)
    db.session.commit()
    return log


def _assert_public_cacheable(resp):
    """公开资源的三条硬约束。"""
    cache_control = resp.headers.get("Cache-Control", "")
    assert "public" in cache_control, f"必须允许 CDN 缓存：{cache_control!r}"
    assert "max-age=" in cache_control, f"必须有明确有效期：{cache_control!r}"
    assert "no-store" not in cache_control and "private" not in cache_control
    vary = resp.headers.get("Vary", "")
    assert "Cookie" not in vary, f"Vary 里带 Cookie 会让 Cloudflare 完全不缓存：{vary!r}"


def test_产出图未登录也能取到(app, client):
    log = _seed_log(app)
    resp = client.get(f"/image-gen/output/{log.id}/0.webp")
    assert resp.status_code == 200
    assert resp.mimetype == "image/webp"
    assert len(resp.data) > 0


def test_产出图带公开缓存头且没有_Vary_Cookie(app, client):
    log = _seed_log(app)
    resp = client.get(f"/image-gen/output/{log.id}/0.webp")
    _assert_public_cacheable(resp)
    assert resp.headers.get("ETag"), "没有 ETag 就无法 304 复用"


def test_参考图未登录也能取到且可缓存(app, client):
    log = _seed_log(app)
    resp = client.get(f"/image-gen/reference/{log.id}/0.webp")
    assert resp.status_code == 200
    _assert_public_cacheable(resp)


def test_ETag_命中返回_304(app, client):
    log = _seed_log(app)
    first = client.get(f"/image-gen/output/{log.id}/0.webp")
    etag = first.headers["ETag"]
    second = client.get(
        f"/image-gen/output/{log.id}/0.webp", headers={"If-None-Match": etag}
    )
    assert second.status_code == 304


def test_静态资源也不带_Vary_Cookie(app, client):
    """静态文件此前同样因为 Vary: Cookie 一直 MISS。"""
    resp = client.get("/static/css/style.css")
    if resp.status_code != 200:
        pytest.skip("该测试环境没有打包静态资源")
    # 这一条才是回归重点：Vary 里一旦出现 Cookie，Cloudflare 就完全不缓存。
    assert "Cookie" not in resp.headers.get("Vary", "")
    cc = resp.headers.get("Cache-Control", "")
    assert "private" not in cc and "no-store" not in cc
    if "max-age=" in cc:
        assert "public" in cc, f"有 max-age 时必须标 public，否则 CDN 不缓存：{cc!r}"


def test_不存在的图片仍然_404(app, client):
    resp = client.get("/image-gen/output/999999/0")
    assert resp.status_code == 404
