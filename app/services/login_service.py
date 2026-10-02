"""登录共用逻辑：账号查找、失败限流与错误文案。

网页登录（app/routes/auth.py）与 App API（app/routes/api.py）必须走同一套判定与限流口径，
避免两端各写一份导致行为不一致。

安全权衡（务必知晓）
--------------------
区分「找不到该用户名/邮箱」与「密码错误」会让攻击者能够批量枚举已注册的账号
（username enumeration）。这是产品明确要求的取舍，因此配套加了按客户端 IP 的失败限流，
把枚举/撞库速度压到每窗口 LOGIN_FAIL_LIMIT 次。
若要恢复不可枚举，把下面两条文案改成同一条即可——这里是唯一改动点。
"""

from flask import request
from sqlalchemy import or_

from ..models import User
from ..utils import rate_hit

# 失败限流：同一客户端 IP 每 LOGIN_FAIL_WINDOW 秒最多 LOGIN_FAIL_LIMIT 次失败登录。
LOGIN_FAIL_SCOPE = "login_fail"
LOGIN_FAIL_LIMIT = 5
LOGIN_FAIL_WINDOW = 60

MSG_THROTTLED = "登录尝试过于频繁，请稍后再试"
MSG_USER_NOT_FOUND = "找不到该用户名/邮箱"
MSG_BAD_PASSWORD = "密码错误"


def _client_key() -> str:
    """限流维度：客户端 IP。

    与 app.utils.rate_hit 的默认口径一致（未登录用户取 remote_addr）。显式传入是为了
    避免「已登录用户再调登录接口」时被误判成按用户 id 计数。
    注意：若部署在反向代理之后且未启用 ProxyFix，remote_addr 会是代理 IP，届时所有用户
    共用同一限流桶；当前部署为 gunicorn/waitress 直连，remote_addr 即客户端 IP。
    """
    return request.remote_addr or "anon"


def find_user_by_identifier(identifier: str):
    """按用户名或邮箱查找账号（网页与 App 同一口径）。"""
    return User.query.filter(or_(User.username == identifier, User.email == identifier)).first()


def login_throttled() -> bool:
    """当前客户端是否因失败次数过多被限流（只查不记录）。"""
    return rate_hit(
        LOGIN_FAIL_SCOPE,
        limit=LOGIN_FAIL_LIMIT,
        per=LOGIN_FAIL_WINDOW,
        key=_client_key(),
        record=False,
    )


def record_login_failure() -> None:
    """记录一次登录失败（计入限流窗口）。成功登录不应调用。"""
    rate_hit(
        LOGIN_FAIL_SCOPE,
        limit=LOGIN_FAIL_LIMIT,
        per=LOGIN_FAIL_WINDOW,
        key=_client_key(),
    )
