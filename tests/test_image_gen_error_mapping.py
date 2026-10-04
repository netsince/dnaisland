"""生图上游错误的「人话翻译」。

背景（实测，见生产库 generation_logs）：
`G Imagine`（= grok-imagine-image）带参考图时 100% 失败，上游固定返回
`Invalid request: multipart: NextPart: EOF`。直连复现确认不是我们的问题：
连 werkzeug 生成的标准 multipart body 也一样被拒，而同一渠道的 JSON 生图接口正常 ——
是那条渠道的图片编辑中继坏了。用户唯一能做的动作是去掉参考图或换模型，所以把它
翻译成「该模型当前不支持参考图。请去掉参考图后重试，或换用其他模型。」。

本文件同时守住反向要求：**别的上游错误一个字都不能改**（不能被翻译吞掉）。
"""

import io
import json
import urllib.error

import pytest
from app import create_app, db
from app.config import Config
from app.services import image_gen_service as svc
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


def _http_error(code, body: bytes):
    """造一个带 body 的 HTTPError（e.read() 能被调一次）。"""
    return urllib.error.HTTPError(
        url="https://upstream.invalid/v1/images/edits",
        code=code,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def test_multipart_eof_is_translated(app):
    """上游真实返回形状（JSON error.message）→ 翻译成人话。"""
    body = json.dumps(
        {
            "error": {
                "message": "Invalid request: multipart: NextPart: EOF",
                "type": "invalid_request_error",
                "param": "",
                "code": None,
            }
        }
    ).encode()
    msg = svc._read_http_error(_http_error(400, body))
    assert msg == svc.REF_UNSUPPORTED_MESSAGE
    assert "该模型当前不支持参考图" in msg
    assert "multipart" not in msg, "对用户不该露出上游原始错误串"
    assert "400" not in msg, "翻译后的提示不该带状态码噪声"


def test_translation_is_case_insensitive_and_matches_partial_text(app):
    """大小写/前后缀不同也要认得（上游不同渠道措辞会变）。"""
    for raw in (
        "multipart: NextPart: EOF",
        "invalid request: MULTIPART: NEXTPART: EOF",
        "something happened: nextpart: eof (relay)",
    ):
        assert svc._friendly_upstream_message(raw) == svc.REF_UNSUPPORTED_MESSAGE, raw


def test_plain_text_body_is_translated_too(app):
    """上游有时返回非 JSON（纯文本）——同样要翻译。"""
    msg = svc._read_http_error(_http_error(400, b"multipart: NextPart: EOF"))
    assert msg == svc.REF_UNSUPPORTED_MESSAGE


def test_other_errors_pass_through_unchanged(app):
    """反向要求：别的错误一个字都不能改（含状态码前缀，便于排查）。"""
    cases = (
        (400, {"error": {"message": "您的请求无法用于生成图像。该请求可能因安全政策被拦截。"}}),
        (504, {"error": {"message": "图片生成超时，请稍后再试。"}}),
        (429, {"error": {"message": "触发了生成频率限制，请稍后再试。"}}),
        (400, {"error": {"message": "Invalid request: image is required"}}),
    )
    for code, payload in cases:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        msg = svc._read_http_error(_http_error(code, raw))
        assert msg == f"生图接口错误 ({code}): {payload['error']['message']}", msg


def test_non_json_non_matching_body_keeps_raw_text(app):
    """非 JSON 且不匹配任何模式：原样带出来（不吞错误）。"""
    msg = svc._read_http_error(_http_error(502, b"<html>bad gateway</html>"))
    assert msg == "生图接口错误 (502): <html>bad gateway</html>"


def test_edit_single_surfaces_the_translated_message(app, monkeypatch):
    """端到端（到 _edit_single 的调用路径）：用户拿到的是翻译后的 RuntimeError。"""
    body = json.dumps({"error": {"message": "Invalid request: multipart: NextPart: EOF"}}).encode()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, io.BytesIO(body))

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as excinfo:
        svc._edit_single(
            "https://upstream.invalid/v1",
            "k",
            "grok-imagine-image",
            "提示词",
            None,
            [("ref.png", b"\x89PNG\r\n\x1a\n", "image/png")],
        )
    assert str(excinfo.value) == svc.REF_UNSUPPORTED_MESSAGE


def test_generate_single_also_translates(app, monkeypatch):
    """无参考图的 JSON 路径若返回同样的错误，也翻译（同一条映射，不区分入口）。"""
    body = json.dumps({"error": {"message": "multipart: NextPart: EOF"}}).encode()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, io.BytesIO(body))

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        svc._generate_single("https://upstream.invalid/v1", "k", "m", "p", None)
    assert str(excinfo.value) == svc.REF_UNSUPPORTED_MESSAGE
