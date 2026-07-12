import click

from .extensions import db
from .models import User


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
