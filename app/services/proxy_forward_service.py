"""转发 API 的 HTTP 中继：路径替换、认证替换、流式透传与审计日志。

转发规则（与产品约定一致）：
- 请求路径开头的平台对外前缀 ``/proxyapi/v1`` 整体替换为用户配置的上游 base URL，
  其余路径 / query / body 原样透传；
- Authorization 头替换为用户上游真实 key；
- 上游响应（含 SSE）逐块流式回传，同时攒齐后成对写入审计日志；
- 日志中永不出现用户上游真实 key。
"""

from urllib.request import urlopen

from flask import current_app

from ..extensions import db
from ..models import ProxyConfig, ProxyLog
from ..models.proxy import TOKEN_PREFIX

UPSTREAM_TIMEOUT = 300  # 秒；覆盖长连接 SSE 与慢生图
CHUNK_SIZE = 8192

# 平台签发的对外 base 路径：站点域名 + 此路径 = 用户拿到的 base URL
PLATFORM_BASE_PATH = "/proxyapi/v1"
# 兜底前缀：客户端少写了 /v1 时也能转发
PLATFORM_MOUNT_PATH = "/proxyapi"

# 逐跳头与需要特殊处理的头不透传
DROP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "authorization",
    "accept-encoding",  # 强制上游回未压缩内容，保证审计日志可读
    "keep-alive",
    "cookie",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
}
DROP_RESPONSE_HEADERS = {
    "connection",
    "transfer-encoding",
    "content-length",  # Flask 按流式重算
    "keep-alive",
}


def remaining_path(request_path: str) -> str:
    """去掉平台前缀，得到要接到上游 base URL 后面的剩余路径。"""
    if request_path.startswith(PLATFORM_BASE_PATH):
        rest = request_path[len(PLATFORM_BASE_PATH) :]
    elif request_path.startswith(PLATFORM_MOUNT_PATH):
        rest = request_path[len(PLATFORM_MOUNT_PATH) :]
    else:
        rest = ""
    return rest or "/"


def build_upstream_url(base_url: str, request_path: str, query_string: bytes = b"") -> str:
    """上游最终 URL = 用户配置的 base + 剩余路径 + 原 query。"""
    base = (base_url or "").strip().rstrip("/")
    url = base + remaining_path(request_path)
    qs = (query_string or b"").decode("latin-1")
    if qs:
        url += "?" + qs
    return url


def pick_forward_headers(headers, upstream_key: str) -> dict:
    """复制入站请求头（丢弃逐跳头/认证头），换成上游认证。"""
    out = {}
    for k, v in headers.items():
        if k.lower() in DROP_REQUEST_HEADERS:
            continue
        out[k] = v
    out["Authorization"] = f"Bearer {upstream_key}"
    return out


def pick_response_headers(headers) -> dict:
    """过滤上游响应头后原样回传。"""
    out = {}
    for k, v in headers.items():
        if k.lower() in DROP_RESPONSE_HEADERS:
            continue
        out[k] = v
    # 部署在 nginx 等反代后面时禁用缓冲，保证 SSE 实时到达客户端
    out.setdefault("X-Accel-Buffering", "no")
    return out


def resolve_config(auth_header):
    """按 Authorization 解析平台令牌并查配置。

    返回 (config, error_message)；error_message 非 None 时 config 为 None。
    """
    if not auth_header or not auth_header.strip():
        return None, "Missing API key"
    token = auth_header.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token.startswith(TOKEN_PREFIX):
        return None, "Invalid API key"
    cfg = ProxyConfig.query.filter_by(token=token).first()
    if cfg is None:
        return None, "Invalid API key"
    if not cfg.enabled:
        return None, "This proxy config has been disabled"
    return cfg, None


def open_upstream(req):
    """发起上游请求（独立封装便于测试替身）。"""
    return urlopen(req, timeout=UPSTREAM_TIMEOUT)


def _persist_log(**fields):
    """写一条审计日志；失败不影响主流程。"""
    try:
        db.session.add(ProxyLog(**fields))
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.warning("转发审计日志落库失败", exc_info=True)


def error_payload(message: str, code: str) -> dict:
    """OpenAI 风格的错误体，客户端 SDK 可正常解析。"""
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "param": None,
            "code": code,
        }
    }
