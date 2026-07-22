"""调用 OpenAI 格式生图接口（兼容任意 OpenAI 兼容端点）。

- 无参考图 -> POST {base_url}/images/generations （JSON）
- 有参考图 -> POST {base_url}/images/edits （multipart/form-data）

仅用标准库 urllib，避免引入额外依赖。支持 ThreadPoolExecutor 并发发起多个单图请求。
"""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import urllib.error
import urllib.request

from PIL import Image


def _build_url(base_url, path):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("未配置生图 API 地址")
    return f"{base}{path}"


def _auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def _png_data_url_to_webp(png_data_url, quality=90):
    """将 PNG base64 data URL 转成 WebP data URL；失败则原样返回。"""
    try:
        _, b64 = png_data_url.split(",", 1)
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=quality)
        webp_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/webp;base64,{webp_b64}"
    except Exception:
        return png_data_url


def _extract_images(payload):
    items = (payload or {}).get("data") or []
    out = []
    for it in items:
        b64 = it.get("b64_json") if isinstance(it, dict) else None
        if b64:
            out.append(_png_data_url_to_webp(f"data:image/png;base64,{b64}"))
    if not out:
        raise RuntimeError("生图接口未返回图像数据")
    return out


def _read_http_error(e):
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        return f"生图接口错误 ({getattr(e, 'code', '?')})"
    try:
        msg = json.loads(body).get("error", {}).get("message") or body
    except Exception:
        msg = body
    return f"生图接口错误 ({getattr(e, 'code', '?')}): {msg}"


def _generate_single(base_url, api_key, model, prompt, size):
    """发起的单张生图请求（n=1）。"""
    url = _build_url(base_url, "/images/generations")
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "quality": "auto",
        "response_format": "b64_json",
    }
    if size:
        body["size"] = size
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **_auth_headers(api_key)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return _extract_images(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise RuntimeError(_read_http_error(e))
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接生图服务：{e.reason}")


def _edit_single(base_url, api_key, model, prompt, size, references):
    """发起的单张参考图生图请求（n=1）。"""
    url = _build_url(base_url, "/images/edits")
    boundary = "----dnaislandBoundary7Qx"
    buf = io.BytesIO()

    def write_field(name, value):
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        buf.write(
            value.encode("utf-8") if isinstance(value, str) else value
        )
        buf.write(b"\r\n")

    write_field("model", model)
    write_field("prompt", prompt)
    write_field("n", "1")
    write_field("quality", "auto")
    write_field("response_format", "b64_json")
    if size:
        write_field("size", size)

    for fname, fbytes, mime in references:
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(
            f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode()
        )
        buf.write(f"Content-Type: {mime}\r\n\r\n".encode())
        buf.write(fbytes)
        buf.write(b"\r\n")

    buf.write(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=buf.getvalue(),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **_auth_headers(api_key),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return _extract_images(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise RuntimeError(_read_http_error(e))
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接生图服务：{e.reason}")


def _single_task(base_url, api_key, model, prompt, size, references):
    if references:
        return _edit_single(base_url, api_key, model, prompt, size, references)
    return _generate_single(base_url, api_key, model, prompt, size)


def generate_images(base_url, api_key, model, prompt, size, n, references):
    """多图请求通过 ThreadPoolExecutor 并发发起 n 次独立的 n=1 请求，确保稳定返回 n 张不同作品。"""
    n = max(1, min(4, int(n or 1)))

    if n == 1:
        return _single_task(base_url, api_key, model, prompt, size, references)

    out = []
    errors = []
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [
            executor.submit(
                _single_task, base_url, api_key, model, prompt, size, references
            )
            for _ in range(n)
        ]
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    out.extend(res)
            except Exception as e:
                errors.append(str(e))

    if not out and errors:
        raise RuntimeError(errors[0])
    return out
