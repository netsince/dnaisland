"""复用连接的测量：把「每次请求都重新握手」这个测量误差剥掉。

真实客户端（Dart HttpClient / 浏览器）对同一主机是**复用连接**的，而标准库
urlopen 每次新建连接 —— 那会把 TLS 握手的开销算进每一个请求。这里用
http.client 手动 keep-alive，看真正的「服务端思考时间 + 传输时间」。
"""

import contextlib
import gzip
import http.client
import statistics
import time
import urllib.parse

HOST = "dnaisland.nb6.ltd"
PATHS = [
    ("site-config", "/api/v1/site-config"),
    ("search/suggest", "/api/v1/search/suggest?q=a"),
    ("cards/featured", "/api/v1/cards/featured"),
    ("cards/explore", "/api/v1/cards/explore"),
    ("teahouse/posts", "/api/v1/teahouse/posts"),
    ("sponsors", "/api/v1/sponsors"),
    ("cards/search", "/api/v1/cards/search?q=" + urllib.parse.quote("爱")),
    ("页面 /teahouse/", "/teahouse/"),
]

HEADERS = {
    "User-Agent": "DNAISLAND-bench/1.0",
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip",
}


def once(conn, path):
    t0 = time.perf_counter()
    conn.request("GET", path, headers=HEADERS)
    resp = conn.getresponse()
    ttfb = (time.perf_counter() - t0) * 1000.0
    raw = resp.read()
    total = (time.perf_counter() - t0) * 1000.0
    if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
        with contextlib.suppress(OSError):
            raw = gzip.decompress(raw)
    return resp.status, ttfb, total, len(raw)


print("=== 复用连接（keep-alive）===")
conn = http.client.HTTPSConnection(HOST, timeout=40)
# 先打一次把 TLS 握手摊掉。
try:
    once(conn, "/api/v1/site-config")
except Exception as exc:  # noqa: BLE001
    print("预热失败:", exc)

for name, path in PATHS:
    samples = []
    status = 0
    for _ in range(3):
        try:
            status, ttfb, total, size = once(conn, path)
            samples.append((ttfb, total, size))
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<22} 失败: {str(exc)[:60]}")
            conn = http.client.HTTPSConnection(HOST, timeout=40)
            break
    if not samples:
        continue
    ttfb = statistics.median([s[0] for s in samples])
    total = statistics.median([s[1] for s in samples])
    size = statistics.median([s[2] for s in samples])
    kbps = (size / 1024.0) / (total / 1000.0) if total > 0 else 0
    print(f"{name:<22} {ttfb:>7.0f}ms  总 {total:>7.0f}ms  {size / 1024.0:>7.1f}KB  {kbps:>6.0f}KB/s  {status}")
conn.close()
