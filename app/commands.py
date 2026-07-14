import click

from .extensions import bcrypt, db
from .models import SiteConfig, User
from .services.site_service import get_site_config


def init_commands(app):
    @app.cli.command("promote-admin")
    @click.argument("username")
    def promote_admin(username: str):
        """将指定用户名提升为 super_admin。"""
        user = db.session.query(User).filter_by(username=username).first()
        if not user:
            raise click.ClickException(f"用户不存在: {username}")
        user.role = "super_admin"
        db.session.commit()
        click.echo(f"已将 {username} 设为 super_admin")

    @app.cli.command("set-role")
    @click.argument("username")
    @click.argument("role")
    def set_role(username: str, role: str):
        """设置用户角色（user / super_admin）。"""
        if role not in ("user", "super_admin"):
            raise click.ClickException("role 只能是 user 或 super_admin")
        user = db.session.query(User).filter_by(username=username).first()
        if not user:
            raise click.ClickException(f"用户不存在: {username}")
        user.role = role
        db.session.commit()
        click.echo(f"已将 {username} 的角色设为 {role}")

    @app.cli.command("init-site")
    def init_site():
        """初始化站点配置（确保 site_config 单行存在）。"""
        cfg = get_site_config()
        db.session.commit()
        click.echo(f"站点配置已就绪（site_name={cfg.site_name}）。")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.argument("email")
    @click.argument("password")
    @click.option("--nickname", default=None, help="昵称（缺省同用户名）")
    def create_admin(username: str, email: str, password: str, nickname: str):
        """创建一个 super_admin 账户（便于首次进入后台）。"""
        if db.session.query(User).filter_by(username=username).first():
            raise click.ClickException(f"用户名已存在: {username}")
        if db.session.query(User).filter_by(email=email).first():
            raise click.ClickException(f"邮箱已存在: {email}")
        u = User(
            username=username,
            nickname=nickname or username,
            email=email,
            email_verified=True,
            role="super_admin",
        )
        u.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        db.session.add(u)
        db.session.commit()
        click.echo(f"已创建超级管理员 {username}（{email}）")
