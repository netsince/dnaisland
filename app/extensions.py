from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()


# 🚫 永久禁用 db.drop_all() —— 防止调试/测试代码意外清空生产数据库
# 如需清空表，请用 db.engine.execute("DROP TABLE ...") 逐个操作
_drop_all_orig = db.drop_all


def _drop_all_disabled(*args, **kwargs):
    raise RuntimeError(
        "db.drop_all() 已被永久禁用。如需清空表，请手动执行 DROP TABLE 语句。"
    )


db.drop_all = _drop_all_disabled  # type: ignore[method-assign]
login_manager = LoginManager()
bcrypt = Bcrypt()
mail = Mail()

login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录后再访问该页面。"
login_manager.login_message_category = "info"
