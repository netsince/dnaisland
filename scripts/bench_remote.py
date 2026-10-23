"""社区远端压测：把 dnaisland.nb6.ltd 上客户端接口、网页接口、页面各跑一遍。

用法（在 dnaisland 目录下）：
    .venv\\Scripts\\python.exe scripts\\bench_remote.py [--runs 3]

设计说明：
* 用标准库 urllib 而不是 requests —— 这个 venv 里没装 requests，而标准库够用，
  还能精确区分 **TTFB**（拿到响应头）和 **总耗时**（读完正文）。
* 主动带 Accept-Encoding: gzip：Dart 的 HttpClient 默认就带，所以真实客户端
  收到的就是压缩后的字节。同时记录线上字节（wire）和解压后字节（body）——
  前者决定「卡不卡」，后者决定「服务端生了多少东西」。
* **串行、少量**：这是线上服务，不做并发压测，只看单请求延迟。
"""

import argparse
import contextlib
import gzip
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://dnaisland.nb6.ltd"
UA = "DNAISLAND-bench/1.0 (latency diagnostics; +https://dnaisland.nb6.ltd)"
TIMEOUT = 40


def _decode(resp, raw):
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    return raw


def fetch(path):
    """返回一次请求的观测值。不抛异常，失败也记下来。"""
    req = urllib.request.Request(
        BASE + path,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ttfb = (time.perf_counter() - t0) * 1000.0
            raw = resp.read()
            total = (time.perf_counter() - t0) * 1000.0
            body = _decode(resp, raw)
            return {
                "status": resp.status,
                "ttfb_ms": ttfb,
                "total_ms": total,
                "wire": len(raw),
                "body": len(body),
            }
    except urllib.error.HTTPError as exc:
        total = (time.perf_counter() - t0) * 1000.0
        raw = exc.read() or b""
        return {
            "status": exc.code,
            "ttfb_ms": total,
            "total_ms": total,
            "wire": len(raw),
            "body": len(raw),
        }
    except Exception as exc:  # noqa: BLE001
        total = (time.perf_counter() - t0) * 1000.0
        return {
            "status": 0,
            "ttfb_ms": total,
            "total_ms": total,
            "wire": 0,
            "body": 0,
            "error": str(exc)[:120],
        }


def get_json(path):
    r = fetch(path)
    if r["status"] != 200:
        return None
    try:
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(BASE + path, headers={"User-Agent": UA})
        ).read())
    except Exception:  # noqa: BLE001
        return None


def resolve_ids():
    """先打几个列表接口，拿真实 id / 用户名，后面测详情页要用。"""
    ids = {}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/api/v1/cards/featured", headers={"User-Agent": UA}
            ),
            timeout=TIMEOUT,
        ) as resp:
            data = json.loads(resp.read())["data"]
            if data:
                ids["card"] = data[0]["id"]
                author = data[0].get("author") or {}
                if author.get("username"):
                    ids["user"] = author["username"]
    except Exception:  # noqa: BLE001
        pass
    for key, path, field in (
        ("article", "/api/v1/articles", "items"),
        ("post", "/api/v1/teahouse/posts", "items"),
        ("topic", "/api/v1/teahouse/topics", None),
    ):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(BASE + path, headers={"User-Agent": UA}),
                timeout=TIMEOUT,
            ) as resp:
                data = json.loads(resp.read())["data"]
                items = data if field is None else data.get(field)
                if items:
                    ids[key] = items[0]["id"]
        except Exception:  # noqa: BLE001
            pass
    return ids


FIXED = [
    ("api", "site-config", "/api/v1/site-config"),
    ("api", "cards/featured", "/api/v1/cards/featured"),
    ("api", "cards/swipe", "/api/v1/cards/swipe"),
    ("api", "cards/explore", "/api/v1/cards/explore"),
    ("api", "cards/explore/meta", "/api/v1/cards/explore/meta"),
    ("api", "cards/search", "/api/v1/cards/search?q=" + urllib.parse.quote("爱")),
    ("api", "recommend", "/api/v1/recommend"),
    ("api", "articles", "/api/v1/articles"),
    ("api", "sponsors", "/api/v1/sponsors"),
    ("api", "reports/meta", "/api/v1/reports/meta"),
    ("api", "teahouse/posts", "/api/v1/teahouse/posts"),
    ("api", "teahouse/topics", "/api/v1/teahouse/topics"),
    ("api", "teahouse/search", "/api/v1/teahouse/search?q=" + urllib.parse.quote("测")),
    ("api", "search/suggest", "/api/v1/search/suggest?q=a"),
    ("api", "users/search", "/api/v1/users/search?q=a"),
    ("api", "notifications/unread-count(匿名401)", "/api/v1/notifications/unread-count"),
    ("api", "my/cards(匿名401)", "/api/v1/my/cards"),
]

DEPENDENT = [
    ("api", "cards/<id>", "/api/v1/cards/{card}"),
    ("api", "cards/<id>/comments", "/api/v1/cards/{card}/comments"),
    ("api", "articles/<id>", "/api/v1/articles/{article}"),
    ("api", "teahouse/posts/<id>", "/api/v1/teahouse/posts/{post}"),
    ("api", "teahouse/topics/<id>", "/api/v1/teahouse/topics/{topic}"),
    ("api", "users/<name>", "/api/v1/users/{user}"),
    ("page", "页面 /", "/"),
    ("page", "页面 /explore", "/explore"),
    ("page", "页面 /teahouse/", "/teahouse/"),
    ("page", "页面 /articles", "/articles"),
    ("page", "页面 /recommend", "/recommend"),
    ("page", "页面 /worlds/", "/worlds/"),
    ("page", "页面 /search", "/search?q=a"),
    ("page", "页面 /card/<id>", "/card/{card}"),
    ("page", "页面 /teahouse/<id>", "/teahouse/{post}"),
    ("page", "页面 /user/<name>", "/user/{user}"),
]


def bench(runs):
    ids = resolve_ids()
    print("解析到的 id: " + json.dumps(ids, ensure_ascii=False))
    print()

    targets = [(k, n, p) for (k, n, p) in FIXED]
    for kind, name, tpl in DEPENDENT:
        try:
            path = tpl.format(**ids)
        except KeyError:
            print("跳过（缺 id）: " + name)
            continue
        targets.append((kind, name, path))

    rows = []
    for kind, name, path in targets:
        fetch(path)  # 预热，不计入
        samples = [fetch(path) for _ in range(runs)]
        ok = [s for s in samples if s["status"] == 200]
        base = ok if ok else samples
        rows.append({
            "kind": kind,
            "name": name,
            "path": path,
            "status": samples[-1]["status"],
            "total_ms": statistics.median([s["total_ms"] for s in base]),
            "ttfb_ms": statistics.median([s["ttfb_ms"] for s in base]),
            "wire_kb": statistics.median([s["wire"] for s in base]) / 1024.0,
            "body_kb": statistics.median([s["body"] for s in base]) / 1024.0,
            "runs": len(samples),
            "error": samples[-1].get("error", ""),
        })
        r = rows[-1]
        print("{:>8.0f}ms  ttfb {:>7.0f}ms  {:>7.1f}KB/{:>7.1f}KB  {} {} {}".format(
            r["total_ms"], r["ttfb_ms"], r["wire_kb"], r["body_kb"],
            r["status"], kind, name))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default="bench_remote.json")
    args = parser.parse_args()
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    rows = bench(args.runs)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)

    print()
    print("=== 按总耗时排序（慢 → 快）===")
    for r in sorted(rows, key=lambda x: -x["total_ms"]):
        print("{:>8.0f}ms  {:>7.1f}KB  {} {}".format(
            r["total_ms"], r["wire_kb"], r["status"], r["name"]))
    print()
    print("结果已写入 " + args.out)


if __name__ == "__main__":
    main()
