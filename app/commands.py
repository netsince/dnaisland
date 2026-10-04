import click

from .extensions import bcrypt, db
from .models import Card, User
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

    @app.cli.command("search-reindex")
    @click.option("--batch", default=200, show_default=True, help="每多少张卡提交一次")
    @click.option(
        "--include-unapproved",
        is_flag=True,
        help="连未通过审核的卡一起索引（默认只索引已通过的）",
    )
    def search_reindex(batch: int, include_unapproved: bool):
        """重建检索倒排索引（首次回填 / 修复用）。

        检索走自带 bigram 倒排 + BM25F（生产 MariaDB 的全文索引对中文片段无效）。
        日常写入由 SQLAlchemy 事件自动维护，本命令用于初次回填与异常修复。
        """
        from .services.search_service import reindex_all

        cards, rows = reindex_all(batch=batch, only_approved=not include_unapproved)
        click.echo(f"已重建 {cards} 张卡的索引，写入 {rows} 条倒排记录。")

    @app.cli.command("search-index-status")
    def search_index_status():
        """查看检索索引状态（文档数 / 倒排行数 / 与已通过卡数的差值）。"""
        from .models import SearchDocStat, SearchGram
        from .services.search_service import DOC_CARD

        docs = (
            db.session.query(db.func.count())
            .select_from(SearchDocStat)
            .filter(SearchDocStat.doc_type == DOC_CARD)
            .scalar()
            or 0
        )
        grams = (
            db.session.query(db.func.count())
            .select_from(SearchGram)
            .filter(SearchGram.doc_type == DOC_CARD)
            .scalar()
            or 0
        )
        approved = (
            db.session.query(db.func.count())
            .select_from(Card)
            .filter(Card.status == "approved")
            .scalar()
            or 0
        )
        click.echo(f"已索引卡片: {docs}")
        click.echo(f"倒排记录行: {grams}")
        click.echo(f"已通过卡片: {approved}")
        if docs < approved:
            click.echo(
                f"提示：有 {approved - docs} 张已通过卡未进索引，"
                "可执行 `flask search-reindex` 回填。"
            )
