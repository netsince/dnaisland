"""可插拔 TTL 缓存。

默认基于进程内 ``OrderedDict`` 实现（单进程 / 单 worker 部署足够）。
对外只暴露 ``get / set / invalidate / clear`` 四个方法，且所有进程内缓存都带
TTL 过期与 LRU 容量上限，防止内存无限增长。

如需跨进程 / 跨机共享，只需把本类替换为 Redis 等后端实现（保持同一接口），
各调用方无需改动。
"""
import time
from collections import OrderedDict
from typing import Any


class TimedCache:
    """带 TTL 过期与 LRU 容量上限的通用缓存。

    - key 过期后 ``get`` 返回 ``None``（视为未命中）
    - 超过 ``maxsize`` 时按插入顺序淘汰最旧项，防止内存无限增长
    - ``invalidate(key)`` 供写路径主动失效
    - ``get(key, stale=True)`` 在过期时仍返回旧值（不删除），供降级兜底使用
    """

    __slots__ = ("ttl", "maxsize", "_store")

    def __init__(self, ttl: float, maxsize: int = 0):
        self.ttl = ttl
        self.maxsize = maxsize
        self._store: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key, *, stale: bool = False):
        item = self._store.get(key)
        if item is None:
            return None
        ts, value = item
        if time.time() - ts < self.ttl:
            return value
        if stale:
            return value
        self._store.pop(key, None)
        return None

    def set(self, key, value):
        self._store[key] = (time.time(), value)
        if self.maxsize > 0:
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def invalidate(self, key):
        self._store.pop(key, None)

    def clear(self):
        self._store.clear()
