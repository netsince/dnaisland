"""转发 API（BYOK 代理）路由。

- /proxy/set        用户自服务设置页（登录）：一个账号一条上游配置。
- /proxyapi/<path>  全量透传端点：按平台签发令牌查配置 → 换认证 → 流式转发。
"""

import json
import time
import urllib.request
from urllib.error import HTTPError, URLError

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required

from ..models.proxy import PUBLIC_BASE_PATH
from ..services.proxy_forward_service import (
    _persist_log,
    build_upstream_url,
    error_payload,
    open_upstream,
    pick_forward_headers,
    pick_response_headers,
    resolve_config,
)
from ..services.proxy_service import (
    decrypt_secret,
    delete_config,
    get_user_config,
    reset_token,
    upsert_config,
)

proxy_bp = Blueprint("proxy", __name__)

# OpenAI 兼容客户端会用到的全部方法都放行
PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@proxy_bp.route("/proxy/set", methods=["GET", "POST"])
@login_required
def set_config():
    cfg = get_user_config(current_user.id)

    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "reset":
            new_token = reset_token(current_user.id)
            if new_token:
                flash("访问令牌已重置，旧令牌立即失效，请更新到你的客户端", "success")
            else:
                flash("尚未创建转发配置", "warning")
            return redirect(url_for("proxy.set_config"))
        if action == "delete":
            if delete_config(current_user.id):
                flash("转发配置已删除（历史审计日志保留）", "success")
            else:
                flash("尚未创建转发配置", "warning")
            return redirect(url_for("proxy.set_config"))

        cfg, error = upsert_config(
            current_user.id,
            upstream_base_url=request.form.get("upstream_base_url"),
            upstream_api_key_plain=request.form.get("upstream_api_key"),
            remark=request.form.get("remark"),
            enabled=request.form.get("enabled") == "1",
        )
        if error:
            flash(error, "warning")
        else:
            flash("转发配置已保存。对外 base URL 与令牌不变，直接继续使用即可", "success")
        return redirect(url_for("proxy.set_config"))

    public_base_url = request.host_url.rstrip("/") + PUBLIC_BASE_PATH
    return render_template(
        "proxy/set.html",
        cfg=cfg,
        public_base_url=public_base_url,
        token=(cfg.token if cfg else None),
    )


@proxy_bp.route("/proxyapi/<path:path>", methods=PROXY_METHODS)
def relay(path):
    """全量透传：/proxyapi/v1/* → 用户上游 base URL + 剩余路径。"""
    started = time.monotonic()
    method = request.method
    inbound_path = request.path  # 完整路径如 /proxyapi/v1/models
    auth_header = request.headers.get("Authorization")

    # 1) 令牌鉴权（失败也记录一条审计日志：令牌快照）
    cfg, err = resolve_config(auth_header)
    if cfg is None:
        status = 403 if err and "disabled" in err else 401
        code = "proxy_disabled" if status == 403 else "invalid_api_key"
        _log_relay(
            cfg=None,
            token=_extract_token(auth_header),
            method=method,
            path=inbound_path,
            upstream_url=None,
            request_body=request.get_data(),
            status_code=status,
            response_body=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=err,
        )
        return (
            json.dumps(error_payload(err, code), ensure_ascii=False),
            status,
            {"Content-Type": "application/json"},
        )

    # 2) 组装上游请求：路径整体替换 + 认证替换
    upstream_key = decrypt_secret(cfg.upstream_api_key)
    upstream_url = build_upstream_url(
        cfg.upstream_base_url, inbound_path, request.query_string
    )
    body = request.get_data()
    headers = pick_forward_headers(request.headers, upstream_key)
    data = body if body else None
    upstream_req = urllib.request.Request(
        upstream_url, data=data, headers=headers, method=method
    )

    # 3) 发起转发
    try:
        resp = open_upstream(upstream_req)
    except HTTPError as e:  # 上游返回错误状态：原样透传
        err_body = e.read()
        _log_relay(
            cfg=cfg,
            token=cfg.token,
            method=method,
            path=inbound_path,
            upstream_url=upstream_url,
            request_body=body,
            status_code=e.code,
            response_body=err_body,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"upstream http {e.code}",
        )
        content_type = (
            e.headers.get_content_type() if e.headers else "application/json"
        )
        return Response(err_body, status=e.code, content_type=content_type)
    except (URLError, TimeoutError, OSError) as e:  # 连接失败/超时
        message = f"无法连接上游服务：{e}"
        _log_relay(
            cfg=cfg,
            token=cfg.token,
            method=method,
            path=inbound_path,
            upstream_url=upstream_url,
            request_body=body,
            status_code=502,
            response_body=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(e)[:500],
        )
        return (
            json.dumps(error_payload(message, "upstream_error"), ensure_ascii=False),
            502,
            {"Content-Type": "application/json"},
        )

    # 4) 成功：流式透传（同时攒齐响应体，结束后成对落库）
    status = getattr(resp, "status", None) or 200
    out_headers = pick_response_headers(resp.headers)

    def generate():
        chunks = []
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                yield chunk
        finally:
            resp.close()
            _log_relay(
                cfg=cfg,
                token=cfg.token,
                method=method,
                path=inbound_path,
                upstream_url=upstream_url,
                request_body=body,
                status_code=status,
                response_body=b"".join(chunks),
                duration_ms=int((time.monotonic() - started) * 1000),
                error=None,
            )

    return Response(
        stream_with_context(generate()), status=status, headers=out_headers
    )


def _extract_token(auth_header):
    """从 Authorization 头提取平台令牌（用于日志快照），无则 None。"""
    if not auth_header:
        return None
    token = auth_header.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def _log_relay(**fields):
    """把字节体解码为文本后写审计日志（与路由解耦，便于复用）。"""
    for key in ("request_body", "response_body"):
        value = fields.get(key)
        if isinstance(value, bytes):
            fields[key] = value.decode("utf-8", "replace")
    fields.setdefault("user_id", None)
    if fields.get("cfg") is not None:
        fields["user_id"] = fields["cfg"].user_id
        fields.setdefault("config_id", fields["cfg"].id)
    fields.pop("cfg", None)
    _persist_log(**fields)
