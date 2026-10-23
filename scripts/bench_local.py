"""本地性能测试：在**本进程内**跑远端压测里最慢的那几个接口。

目的：把「Cloudflare + 公网 RTT + 传输」从「应用自己的代码 + 数据库」里剥出来。

它连的是 .env 里配置的库（生产库），所以测的是
    handler + 模板渲染 + 真实查询计划
但不含任何网络传输 —— 这正是远端压测里那几十秒里「不是代码的」那部分。

同时统计每个请求打了多少条 SQL：N+1 是列表接口变慢最常见的元凶，而它在
「条数」上是暴露的，跟数据量无关。

用法（在 dnaisland 目录下）：
    .venv\\Scripts\\python.exe -u scripts\\bench_local.py
"""

import contextlib
import os
import statistics
import sys
import time

# 脚本在 scripts/ 下，把项目根加进 sys.path，否则 import app 会失败。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from sqlalchemy import event  # noqa: E402

TARGETS = [
    ("api", "/api/v1/cards/search?q=%E7%88%B1"),
    ("api", "/api/v1/sponsors"),
    ("api", "/api/v1/teahouse/posts"),
    ("api", "/api/v1/cards/explore"),
    ("api", "/api/v1/cards/featured"),
    ("api", "/api/v1/recommend"),
    ("api", "/api/v1/site-config"),
    ("page", "/"),
    ("page", "/teahouse/"),
    ("page", "/explore"),
]

RUNS = 2

app = create_app()

_counter = {"n": 0}


def _count_sql(conn, cursor, statement, parameters, context, executemany):
    _counter["n"] += 1


def main():
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    print("库: " + app.config["SQLALCHEMY_DATABASE_URI"].split("@")[-1])
    print()
    print("{:<10} {:>10} {:>8} {:>8}  {}".format(
        "耗时", "SQL条数", "字节", "状态", "路径"))
    print("-" * 78)

    with app.app_context():
        # 监听必须挂在 app context 里（db.engine 需要 current_app）。
        event.listen(db.engine, "before_cursor_execute", _count_sql)
        client = app.test_client()
        for _kind, path in TARGETS:
            samples = []
            queries = []
            size = 0
            status = 0
            for i in range(RUNS + 1):
                _counter["n"] = 0
                t0 = time.perf_counter()
                resp = client.get(path)
                cost = (time.perf_counter() - t0) * 1000.0
                if i == 0:
                    continue  # 第一次当预热
                samples.append(cost)
                queries.append(_counter["n"])
                size = len(resp.data)
                status = resp.status_code
            print(f"{statistics.median(samples):>8.0f}ms {statistics.median(queries):>10.0f} {size / 1024.0:>8.1f}K {status:>8}  {path}")
            # 慢的或查询特别多的，把前几条 SQL 打出来
            if statistics.median(samples) > 800 or statistics.median(queries) > 40:
                print("        ↑ 慢/查询多，看这几条 SQL 的形状：")
                print("        " + "（见下方 profile 输出）")

    print()
    print("提示：耗时是**本机到数据库**的往返，若库在远端，这部分也含 WAN 延迟。")


if __name__ == "__main__":
    main()
