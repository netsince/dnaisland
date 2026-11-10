"""TimedCache 容量上限语义测试。

核心不变量：**缓存的常驻内存由其内容体积决定，而不是由条目数决定**。
历史缺陷：maxsize 只数条数，图片缓存 500 条 x 平均 1.4MB = 数百 MB 常驻，
条数上限对体积完全失明。以下测试若把 maxbytes 支持移除即会变红。
"""

import time

import pytest

from app.caching import TimedCache, _default_sizeof


def test_get_set_and_ttl_expiry():
    c = TimedCache(ttl=0.05)
    c.set("k", 1)
    assert c.get("k") == 1
    time.sleep(0.06)
    assert c.get("k") is None


def test_stale_returns_old_value_on_expiry():
    c = TimedCache(ttl=0.05)
    c.set("k", "v")
    time.sleep(0.06)
    # stale 返回旧值用于降级，但不删除；普通 get 已视为未命中
    assert c.get("k", stale=True) == "v"
    assert c.get("k") is None


def test_lru_eviction():
    c = TimedCache(ttl=60, maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # 超过 maxsize，最旧的 a 被淘汰
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_invalidate_and_clear():
    c = TimedCache(ttl=60)
    c.set("k", 1)
    c.invalidate("k")
    assert c.get("k") is None
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


# ---------------------------------------------------------------------------
# maxbytes：体积预算（本次修复的核心）
# ---------------------------------------------------------------------------


def test_maxbytes_caps_total_size_not_just_count():
    """条目数远未触顶，但体积超预算时必须淘汰。

    把 maxbytes 逻辑删掉后，此断言会红：3 条 x 1MB 会全留在缓存里。
    """
    c = TimedCache(ttl=60, maxsize=100, maxbytes=3 * 1024 * 1024)
    chunk = b"x" * (1024 * 1024)  # 每条约 1MB
    for i in range(3):
        c.set(i, chunk)
    assert c.nbytes <= 3 * 1024 * 1024
    # 再塞一条：必须淘汰旧项，总字节仍不超预算
    c.set("new", chunk)
    assert c.nbytes <= 3 * 1024 * 1024
    assert len(c) <= 3


def test_maxbytes_evicts_least_recently_used():
    """淘汰按使用序而不是插入序：热条目在冷条目之后被淘汰。

    把 get() 里的 move_to_end 删掉，此测试会红（a 会被淘汰而不是 b）。
    """
    c = TimedCache(ttl=60, maxbytes=2 * 1024 * 1024)
    chunk = b"x" * (1024 * 1024)
    c.set("a", chunk)
    c.set("b", chunk)
    c.get("a")  # 触碰 a，使其成为最近使用
    c.set("c", chunk)  # 触发淘汰，应淘汰 b 而不是 a
    assert c.get("a") is not None
    assert c.get("b") is None


def test_oversized_single_value_is_not_cached():
    """单条超过整个预算的值直接不缓存，而不是先占满再被自己挤出。

    删掉 set() 里的 size > maxbytes 分支后，此断言仍可能通过（因为淘汰会兜底），
    因此额外断言缓存为空 —— 那条值连一次都不该待在里面。
    """
    c = TimedCache(ttl=60, maxbytes=1024)
    c.set("huge", b"x" * 2048)
    assert c.get("huge") is None
    assert len(c) == 0
    assert c.nbytes == 0


def test_overwrite_does_not_double_count_bytes():
    """同一个 key 覆盖写必须减去旧体积，否则预算会被幽灵字节吃光。

    删掉 set() 里的 self._drop(key) 后，此测试会红。
    """
    c = TimedCache(ttl=60, maxbytes=10 * 1024 * 1024)
    for _ in range(50):
        c.set("same", b"x" * 1024)
    assert c.nbytes == 1024
    assert len(c) == 1


def test_nbytes_tracks_invalidation_and_clear():
    """体积计数必须随invalidate/clear同步归零，否则会累积漂移。"""
    c = TimedCache(ttl=60)
    c.set("a", b"x" * 100)
    c.set("b", b"y" * 200)
    assert c.nbytes == 300
    c.invalidate("a")
    assert c.nbytes == 200
    c.clear()
    assert c.nbytes == 0


def test_expired_entries_are_swept_on_set():
    """过期项在 set 时被主动清理，而不是等到被 get 撞到。

    删掉 _evict() 里的 expired 扫描后，此测试会红：冷数据会一直占着预算。
    """
    c = TimedCache(ttl=0.05, maxbytes=10 * 1024 * 1024)
    for i in range(5):
        c.set(i, b"x" * 1024)
    time.sleep(0.06)
    c.set("fresh", b"y" * 1024)
    # 5 个过期项应被清走，只剩新写入的这一条
    assert len(c) == 1
    assert c.nbytes == 1024


# ---------------------------------------------------------------------------
# sizeof 估算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (b"abc", 3),
        (bytearray(b"abcd"), 4),
        ("hello", 5),
    ],
)
def test_sizeof_primitives(value, expected):
    assert _default_sizeof(value) == expected


def test_sizeof_nested_containers_are_summed():
    """容器体积必须把成员算进去，否则 dict 装的图片会被估成 0。"""
    v = {"a": b"x" * 100, "b": b"y" * 200}
    assert _default_sizeof(v) >= 300


def test_sizeof_custom_hook_is_used():
    """允许调用方传入自定义 sizeof，用于估算非 bytes 的昂贵结构。"""
    c = TimedCache(ttl=60, maxbytes=100, sizeof=lambda v: v["weight"])
    c.set("k", {"weight": 60, "payload": object()})
    assert c.nbytes == 60
