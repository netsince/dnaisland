"""网页端静态资源必须走本站 vendor，不许再引外部 CDN。

2026-11 事故：一位用户所在线路到 cdn.jsdelivr.net 不通，Bootstrap、图标字体、
灯箱、裁剪器全部加载失败，页面几乎没法用。资源已经全部下载到
`app/static/vendor/`，由 `staticv()` 带内容指纹提供。

这个文件守住三件事：

1. 模板与 `css/style.css` 里不许再出现外部 CDN 域名 —— 新页面也不会悄悄加回来；
2. 首页拿到的 HTML 里引用的确实是 `/static/vendor/...`；
3. vendor 文件本身能被 Flask 正常发出（bootstrap-icons 的字体是按相对路径
   `fonts/...` 引的，目录搬错就会 404，那比引外链还难查）。
"""

from pathlib import Path

import pytest
from app import create_app, db
from app.config import Config
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 曾经用过的全部外部静态资源域名。
BANNED_HOSTS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "cdn.staticfile.net",
    "cdn.staticfile.org",
    "cdn.bootcdn.net",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
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


@pytest.fixture
def client(app):
    return app.test_client()


def test_templates_and_css_never_reference_external_cdn():
    """模板与样式表里不许再出现外部 CDN 域名（离线/被墙都必须能开）。"""
    targets = sorted(APP_DIR.glob("templates/**/*.html")) + [APP_DIR / "static" / "css" / "style.css"]
    assert len(targets) > 50, "扫描目标数量不对，路径可能改过了"

    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for host in BANNED_HOSTS:
            if host in text:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}: {host}")

    assert not offenders, "这些文件还在引外部 CDN：\n" + "\n".join(offenders)


def test_homepage_points_at_local_vendor_assets(app, client):
    """首页实际引用的是本站 vendor，而不是外链。"""
    html = client.get("/").get_data(as_text=True)
    assert "/static/vendor/bootstrap/bootstrap.min.css" in html
    assert "/static/vendor/bootstrap-icons/bootstrap-icons.min.css" in html
    assert "/static/vendor/bootstrap/bootstrap.bundle.min.js" in html


def test_vendor_assets_are_served_by_flask(app, client):
    """vendor 文件本身要能正常发出（含 bootstrap-icons 的字体文件）。"""
    cases = [
        ("vendor/bootstrap/bootstrap.min.css", "css"),
        ("vendor/bootstrap/bootstrap.bundle.min.js", "javascript"),
        ("vendor/bootstrap-icons/bootstrap-icons.min.css", "css"),
        ("vendor/glightbox/glightbox.min.css", "css"),
        ("vendor/glightbox/glightbox.min.js", "javascript"),
        ("vendor/cropperjs/cropper.min.css", "css"),
        ("vendor/cropperjs/cropper.min.js", "javascript"),
        ("vendor/chartjs/chart.umd.js", "javascript"),
        ("vendor/bootstrap-icons/fonts/bootstrap-icons.woff2", "font"),
        ("vendor/bootstrap-icons/fonts/bootstrap-icons.woff", "font"),
    ]
    for rel, kind in cases:
        r = client.get("/static/" + rel)
        assert r.status_code == 200, rel
        assert len(r.data) > 500, rel
        assert kind in r.mimetype, f"{rel} 的 Content-Type 是 {r.mimetype}"
