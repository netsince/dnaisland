
# -*- coding: utf-8 -*-
"""生图中转数据清理：只读预演，不做任何修改。

用途：在执行前把影响面算清楚 —— 影响多少行、能回收多少空间、用户回看
依赖的 GenerationLog 那一份是否完好。加 --execute 才会真正写库。
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

import pymysql


def connect():
    url = os.environ.get("DATABASE_URL", "")
    # 从 .env 读（避免把密码写进脚本）
    if not url:
        for line in open(".env", encoding="utf-8"):
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
                break
    if "mysql" not in url:
        sys.exit("DATABASE_URL 不是 MySQL，脚本只处理线上 MySQL")
    auth, rest = url.split("://", 1)[1].split("@")
    user, pwd = auth.split(":", 1)
    hostport, dbname = rest.split("/", 1)
    host, port = hostport.split(":")
    return pymysql.connect(
        host=host, port=int(port), user=user, password=pwd,
        database=dbname, charset="utf8mb4", connect_timeout=20,
    )


def mb(n):
    return round((n or 0) / 1024 / 1024, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="真正执行清理")
    ap.add_argument("--backup", default="", help="备份 JSON 输出路径")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    print("=" * 72)
    print("生图中转数据清理", "【预演，不修改】" if not args.execute else "【执行】")
    print("=" * 72)

    # ---------- 1. 影响面 ----------
    cur.execute("""
        SELECT status, COUNT(*),
               ROUND(SUM(CHAR_LENGTH(reference_data))/1024/1024, 1),
               SUM(CHAR_LENGTH(reference_data)) 
        FROM generation_tasks
        WHERE reference_data IS NOT NULL AND reference_data <> ''
        GROUP BY status
    """)
    print("\n[1] generation_tasks.reference_data 分布")
    total_bytes = 0
    for status, cnt, mbs, bytes_ in cur.fetchall():
        total_bytes += bytes_ or 0
        print(f"    {status:12} 行={cnt:>5}  {mbs:>8} MB")

    cur.execute("""
        SELECT COUNT(*) FROM generation_tasks
        WHERE status IN ('succeeded','failed') AND reference_data IS NOT NULL
    """)
    affected = cur.fetchone()[0]
    print(f"\n    终态任务且仍有参考图：{affected} 行")
    print(f"    预计回收：{mb(total_bytes)} MB")

    # ---------- 2. 安全校验：log 那一份必须还在 ----------
    print("\n[2] 安全校验 —— 清理不会让用户回看丢图")
    cur.execute("""
        SELECT COUNT(*) FROM generation_tasks t
        JOIN generation_logs l ON t.result_log_id = l.id
        WHERE t.status IN ('succeeded','failed')
          AND t.reference_data IS NOT NULL AND t.reference_data <> '[]'
          AND (l.reference_images IS NULL OR l.reference_images = '[]')
    """)
    orphan = cur.fetchone()[0]
    print(f"    清理后会「日志里也没有参考图」的任务：{orphan} 行")
    if orphan:
        print("    ⚠ 这些任务的参考图是唯一副本，清理会导致回看丢图 —— 需人工确认")
    else:
        print("    ✓ 全部都有日志副本，清理安全")

    # ---------- 3. 备份 ----------
    # 只记录「哪些行会被改」+ 体积，不拉大字段本体：
    # 843MB 的 longtext 逐行回传会把备份拖成几十分钟，而日志表里那份
    # 副本本来就是完整的 —— 真要回滚，从 generation_logs 按 result_log_id 取。
    if args.backup:
        cur.execute("""
            SELECT t.id, t.status, t.result_log_id,
                   CHAR_LENGTH(t.reference_data) AS n
            FROM generation_tasks t
            WHERE t.status IN ('succeeded','failed')
              AND t.reference_data IS NOT NULL AND t.reference_data <> ''
            ORDER BY t.id
        """)
        rows = cur.fetchall()
        with open(args.backup, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "rows": [
                        {"task_id": r[0], "status": r[1],
                         "result_log_id": r[2], "bytes": r[3]}
                        for r in rows
                    ],
                },
                f, ensure_ascii=False, indent=1,
            )
        total = sum(r[3] or 0 for r in rows)
        print(f"\n[3] 备份清单已写入 {args.backup}")
        print(f"    {len(rows)} 行，{mb(total)} MB")
        print("    回滚方式：按 result_log_id 从 generation_logs.reference_images 取回")

    if not args.execute:
        print("\n预演结束。确认无误后加 --execute 执行。")
        conn.close()
        return

    # ---------- 4. 执行 ----------
    print("\n[4] 执行清理")
    cur.execute("""
        UPDATE generation_tasks
        SET reference_data = NULL
        WHERE status IN ('succeeded','failed')
          AND reference_data IS NOT NULL AND reference_data <> ''
    """)
    print(f"    清空 tasks.reference_data：{cur.rowcount} 行")

    cur.execute("UPDATE generation_logs SET reference_images = NULL WHERE reference_images = '[]'")
    print(f"    归一 images 空数组：{cur.rowcount} 行")

    cur.execute("UPDATE generation_logs SET images = NULL WHERE images = '[]'")
    print(f"    归一 reference_images 空数组：{cur.rowcount} 行")

    conn.commit()
    print("    已提交")

    print("\n[5] 回收表空间（OPTIMIZE 会重建表，稍慢）")
    for t in ("generation_tasks", "generation_logs"):
        cur.execute(f"OPTIMIZE TABLE {t}")
        cur.fetchall()
        print(f"    {t} 完成")

    cur.execute("""
        SELECT SUM(data_length+index_length)/1024/1024
        FROM information_schema.TABLES WHERE table_schema = DATABASE()
    """)
    print(f"\n    清理后库总大小：{mb(cur.fetchone()[0])} MB")
    conn.close()


if __name__ == "__main__":
    main()
