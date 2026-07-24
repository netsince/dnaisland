from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user

from .utils import is_xhr


def super_admin_required(f):
    """仅允许 super_admin 访问；其余已登录用户跳转首页，未登录跳转登录。"""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            from flask import flash, redirect, url_for

            flash("请先登录", "warning")
            return redirect(url_for("auth.login"))
        if not current_user.is_super_admin:
            from flask import abort

            abort(403)
        return f(*args, **kwargs)

    return decorated


def block_if_muted(message="你已被禁言，暂时无法执行该操作", redirect_endpoint="main.index"):
    """被禁言（且非超级管理员）的用户，拦截其写操作。

    放在 @login_required 之外层（即更靠近路由装饰器），确保先完成登录校验。
    JSON 请求返回 403 + {ok:false,error}；普通请求 flash 后跳回来源页。
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if (
                current_user.is_authenticated
                and current_user.is_muted
                and not current_user.is_super_admin
            ):
                if is_xhr():
                    return jsonify(ok=False, error=message), 403
                flash(message, "warning")
                return redirect(request.referrer or url_for(redirect_endpoint))
            return f(*args, **kwargs)

        return wrapper

    return decorator
