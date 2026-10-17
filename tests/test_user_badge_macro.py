"""user_badge 宏：class 属性必须真的生效。

真事故：宏里曾把属性拼成一个字符串再 {{ _attrs }} 输出，Jinja 会把里面的双引号
转义成 &#34; —— 浏览器解析出的 class 只剩「"text-decoration-none」，后面的
dna-chip 变成一个无意义的伪属性，于是**作者那一行的药丸整个消失**，text-reset
也没生效（作者名一直是个带下划线的链接）。

这个宏被全站所有用户名展示复用（角色卡 / 文章 / 茶馆 / 世界卡），所以守一条
就够了。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from flask import render_template_string
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


def _render(app, call):
    """[call] 是宏调用源码，例如 'user_badge(u, class="dna-chip")'。

    Jinja 不支持在调用处展开 **kwargs，所以这里直接拼调用串。
    """
    with app.test_request_context():
        u = User(username="alice", nickname="Alice")
        u.id = 1
        return render_template_string(
            '{% from "macros/user_badge.html" import user_badge with context %}'
            "{{ " + call + " }}",
            u=u,
        )


def test_class_attribute_is_not_escaped(app):
    """class 里的双引号不能被转义，否则 dna-chip 不生效。"""
    html = _render(app, 'user_badge(u, class="dna-chip")')
    assert 'class="text-decoration-none text-reset dna-chip"' in html
    assert "&#34;" not in html


def test_title_is_rendered_as_real_attribute(app):
    html = _render(app, 'user_badge(u, class="dna-chip", title="作者")')
    assert 'title="作者"' in html
    assert "&#34;" not in html


def test_no_class_passed_still_gets_base_classes(app):
    html = _render(app, "user_badge(u)")
    assert 'class="text-decoration-none text-reset"' in html
