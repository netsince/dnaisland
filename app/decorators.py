from functools import wraps

from flask_login import current_user


def super_admin_required(f):
    """仅允许 super_admin 访问；其余已登录用户跳转首页，未登录跳转登录。"""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            from flask import redirect, url_for, flash

            flash("请先登录", "warning")
            return redirect(url_for("auth.login"))
        if not current_user.is_super_admin:
            from flask import abort

            abort(403)
        return f(*args, **kwargs)

    return decorated
