import time

from app.caching import TimedCache


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
