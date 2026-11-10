"""可插拔 TTL 缓存。

默认基于进程内 OrderedDict 实现（单进程 / 单 worker 部署足够）。
对外只暴露 get / set / invalidate / clear 四个方法，且所有进程内缓存都带
TTL 过期与容量上限，防止内存无限增长。

容量上限有两个维度，任一命中即触发淘汰：

* maxsize  -- 条目数上限
* maxbytes -- 常驻字节预算（由 sizeof 估算）

为什么必须有 maxbytes：条目数相等不等于占用相等。图片缓存按 500 条封顶，
若单条平均 1.4MB，常驻就是约 700MB —— 条数上限对体积完全失明。进程内存必须由
「装了多少字节」决定，而不是「装了几个」。见 services/image_service.py 的
_WEBP_CACHE：它缓存的是整张图片字节，是本类 maxbytes 存在的直接原因。

如需跨进程 / 跨机共享，只需把本类替换为 Redis 等后端实现（保持同一接口），
各调用方无需改动。
"""

import time
from collections import OrderedDict
from typing import Any

# 估算容器体积时的递归深度上限：防止病态嵌套结构把一次 set 变成全图遍历。
_MAX_SIZEOF_DEPTH = 3


def _default_sizeof(value: Any, _depth: int = 0) -> int:
    """估算值常驻内存的字节数。

    对 bytes / bytearray / str 取长度（str 按字符数计，base64 与英文场景下与字节数
    基本一致）；对常见容器按「头部 + 各项之和」近似，足以让字节预算正确工作，
    不追求精确。

    不做精确统计是刻意的：本函数在每次 set 时同步调用，必须是 O(条目) 而非
    O(字节)；对图片这种单条就是 bytes 的情况（最常见）结果本来就是精确的。
    """
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value)
    if _depth >= _MAX_SIZEOF_DEPTH:
        return 64
    if isinstance(value, dict):
        return 64 + sum(
            _default_sizeof(k, _depth + 1) + _default_sizeof(v, _depth + 1)
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return 64 + sum(_default_sizeof(v, _depth + 1) for v in value)
    return 64


class TimedCache:
    """带 TTL 过期与容量上限的通用缓存。

    - key 过期后 get 返回 None（视为未命中）
    - 超过 maxsize 或 maxbytes 时按「最久未使用」淘汰
    - invalidate(key) 供写路径主动失效
    - get(key, stale=True) 在过期时仍返回旧值（不删除），供降级兜底使用

    淘汰策略是真正的 LRU（命中即刷新访问序），而不是插入序 —— 否则热点条目会和
    冷条目一起被淘汰，缓存命中率下降却没有换来内存收益。
    """

    __slots__ = ("ttl", "maxsize", "maxbytes", "sizeof", "_store", "_bytes")

    def __init__(
        self,
        ttl: float,
        maxsize: int = 0,
        maxbytes: int = 0,
        sizeof=None,
    ):
        """maxsize / maxbytes 为 0 表示该维度不限制；两者都为 0 时仅靠 TTL 惰性
        过期淘汰。生产代码不应同时留 0 —— 那等于放弃容量上限。"""
        self.ttl = ttl
        self.maxsize = maxsize
        self.maxbytes = maxbytes
        self.sizeof = sizeof or _default_sizeof
        self._store: OrderedDict[Any, tuple[float, Any, int]] = OrderedDict()
        self._bytes = 0

    @property
    def nbytes(self) -> int:
        """当前常驻体积估算（字节）。用于监控与测试断言。"""
        return self._bytes

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key, *, stale: bool = False):
        item = self._store.get(key)
        if item is None:
            return None
        ts, value, _size = item
        if time.time() - ts < self.ttl:
            # 命中即刷新访问序：让淘汰真正按使用频率而不是插入顺序发生
            self._store.move_to_end(key)
            return value
        if stale:
            return value
        self._drop(key)
        return None

    def set(self, key, value):
        size = self.sizeof(value)
        # 单条就超出整个预算：直接不缓存。否则它会瞬间把预算占满、挤掉所有
        # 其他条目，而自己下一次写入又要被淘汰，等于缓存完全失效还多付清空成本。
        if self.maxbytes > 0 and size > self.maxbytes:
            self._drop(key)
            return
        self._drop(key)  # 覆盖写时先减去旧体积，避免重复计数
        self._store[key] = (time.time(), value, size)
        self._bytes += size
        self._evict()

    def _drop(self, key) -> None:
        item = self._store.pop(key, None)
        if item is not None:
            self._bytes -= item[2]

    def _evict(self) -> None:
        """先清过期项，再按 LRU 淘汰到预算内。

        过期清理放在这里而不是只在 get 做，是因为惰性删除只在「被别人访问到」
        时才生效：只被读过一次的冷数据会一直占着内存直到 TTL 之后很久。
        """
        now = time.time()
        expired = [k for k, (ts, _v, _s) in self._store.items() if now - ts >= self.ttl]
        for key in expired:
            self._drop(key)
        while self._store and (
            (self.maxsize > 0 and len(self._store) > self.maxsize)
            or (self.maxbytes > 0 and self._bytes > self.maxbytes)
        ):
            self._drop(next(iter(self._store)))

    def invalidate(self, key) -> None:
        self._drop(key)

    def clear(self) -> None:
        self._store.clear()
        self._bytes = 0
