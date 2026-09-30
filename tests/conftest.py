"""pytest 全局配置。"""

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """清空进程内限流状态（app.utils._RATE_LIMITS）。

    限流状态是模块级全局字典。登录失败限流上线后，若不在测试之间清空，某个用例里的失败
    登录会污染后续用例的登录，产生与断言无关的随机失败。这里在每个用例前后各清一次。
    """
    from app.utils import _RATE_LIMITS

    _RATE_LIMITS.clear()
    yield
    _RATE_LIMITS.clear()
