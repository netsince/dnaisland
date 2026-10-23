import json

from sqlalchemy import and_, or_
from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db


class WorldCard(db.Model):
    """社区里的「世界卡」（世界书）。

    ## payload 原样存原文

    客户端 World.toJson() 出来是什么文本，进来什么样、出去什么样；服务端
    **不解析词条结构**。

    这一点是刻意的：词条字段（正则激活键 keyRegex、递归扫描 recursive、冷却
    与延迟轮数 cooldownRounds/delayRounds、装饰符 decorator、排序 order……）
    还会继续长。服务端一旦去解析并校验 schema，每加一个字段就要改后端 +
    迁移 —— 那才是真正的限制。所以服务端只从原文里读几个**顶层**字段
    （name / summary / tags / entries.length）做列表，其余一律不碰。
    """

    __tablename__ = "world_cards"

    # 平台自动分配的主键（UUID）。**绝不读客户端原文里的 id** —— 本地世界和
    # 社区世界是两套 id，混用会撞（导入时必须重新生成，见 WorldCopyStat 的说明）。
    id = db.Column(db.String(36), primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # ---- 列表用的元信息（发布时从原文顶层抄一份；列表接口不碰 payload）----
    name = db.Column(db.String(120), nullable=False)
    summary = db.Column(db.Text, server_default="")
    entry_count = db.Column(db.Integer, nullable=False, server_default="0")
    payload_bytes = db.Column(db.Integer, nullable=False, server_default="0")

    # ---- 列表卡片用的词条名预览 ----
    # 存发布/编辑时算好的 JSON 数组（前若干条词条名）。**不在列表接口里解析
    # payload**：一页 20 个世界、每个几百 KB，解析一遍就是几十 MB 的 CPU，
    # 而卡片上只需要几个名字。
    preview_names = db.Column(db.Text, server_default="[]")

    # ---- 原文 ----
    # 用 LONGTEXT 而不是 db.Text：MySQL 的 TEXT 只有 64KB，几百 KB 的世界书
    # 会直接存不进去。这里**不设产品级体积上限**，让基础设施的天花板说话
    # （请求体另有 MAX_CONTENT_LENGTH = 16MB 兜底）。
    # deferred：列表/详情元信息查询不会把这段大文本一起拉出来。
    payload = db.deferred(db.Column(LONGTEXT, nullable=False))

    # ---- 计数 ----
    view_count = db.Column(db.Integer, server_default="0")
    # 复制量：口径与 Card.copy_count 一致 —— 同一用户对同一个世界卡每天最多 +1；
    # 明细见 WorldCopyStat（每次复制都记一条，不受去重影响）。
    copy_count = db.Column(db.Integer, server_default="0")

    # ---- 治理 ----
    status = db.Column(db.String(20), server_default="pending")
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    # 内容指纹（原文的 sha256）：同一作者重复提交同样的世界时幂等复用，
    # 不再新建一条待审核记录（与 Card.content_hash 同一套做法）。
    content_hash = db.Column(db.String(64), nullable=True, index=True)

    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )

    author = db.relationship("User", backref="world_cards")

    @property
    def preview_list(self):
        """词条名预览（模板直接读）。解析失败一律降级成空。"""
        try:
            names = json.loads(self.preview_names or "[]")
        except (ValueError, TypeError):
            return []
        if not isinstance(names, list):
            return []
        return [str(n) for n in names if str(n).strip()]

    @classmethod
    def visible_to(cls, viewer=None):
        """信息层面的可见性过滤，口径与 Card.visible_to 完全一致。

        - 作者本人：可查看自己的世界（含 pending 待审核）
        - 超级管理员：可查看所有未硬删除的
        - 普通访客/其他用户：仅 status == 'approved'、未隐藏、作者未被处罚
        """
        from .punishment import Punishment
        from .user import User

        q = cls.query.join(User, cls.author_id == User.id).filter(
            cls.is_hidden.is_(False),
            User.status != "admin_del",
        )
        hidden_ids = (
            db.session.query(Punishment.user_id)
            .filter(Punishment.status == "active", Punishment.type == "hide_cards")
            .distinct()
        )
        if viewer is not None and getattr(viewer, "is_authenticated", False):
            if viewer.is_super_admin:
                return q
            return q.filter(
                or_(
                    cls.author_id == viewer.id,
                    and_(
                        cls.status == "approved",
                        cls.author_id.notin_(hidden_ids),
                    ),
                )
            )
        return q.filter(cls.status == "approved", cls.author_id.notin_(hidden_ids))

    @property
    def is_public(self):
        """单个世界卡是否对公众可见（与 visible_to 口径一致）。

        用于详情 / 取原文等单卡访问的 404 拦截。
        """
        author = self.author
        return (
            self.status == "approved"
            and not self.is_hidden
            and not (author and (author.is_cards_hidden or author.is_profile_banned))
        )


class WorldTag(db.Model):
    """世界卡标签（与 CardTag 同构，列表筛选与搜索用）。"""

    __tablename__ = "world_tags"

    world_id = db.Column(
        db.String(36), db.ForeignKey("world_cards.id"), primary_key=True
    )
    tag = db.Column(db.String(50), primary_key=True, index=True)


class WorldFavorite(db.Model):
    """世界卡收藏。

    与 CardFavorite 同构。为什么世界卡做收藏而不做点赞：收藏的语义是
    「我以后要导入它」，正好对上世界书的使用方式；点赞没有对应的动作。
    """

    __tablename__ = "world_favorites"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    world_id = db.Column(
        db.String(36), db.ForeignKey("world_cards.id"), primary_key=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class WorldCopyStat(db.Model):
    """世界卡复制（导出原文）统计。

    与 CardCopyStat 同构：每次用户「复制世界卡」（取一次原文）时记一条，
    含世界名与用户名快照 + 来源 IP，用于版权溯源；即使后续改名/删除也能
    追溯当时信息。
    """

    __tablename__ = "world_copy_stats"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(
        db.String(36), db.ForeignKey("world_cards.id"), nullable=False, index=True
    )
    # 世界名快照（复制时的名称，后续改名不影响历史记录）
    world_name = db.Column(db.String(120), nullable=False, server_default="")
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    # 复制者用户名快照
    username = db.Column(db.String(120), nullable=False, server_default="")
    copied_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False, index=True
    )
    # 复制者来源 IP，用于版权溯源
    copier_ip = db.Column(db.String(64), nullable=True)

    user = db.relationship("User", backref="world_copy_stats")
    world = db.relationship("WorldCard", backref="copy_stats")
