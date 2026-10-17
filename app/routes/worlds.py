"""社区「世界卡」的 App JSON 接口（/api/v1/worlds）。

## 核心：payload 是原文

发布时客户端把 World.toJson() 的文本**原样**作为请求体发过来（不套 JSON 信封，
免得把几百 KB 的文本再转义一遍）；服务端只从里面读几个**顶层**字段
（name / summary / tags / entries.length）填列表用的元信息，其余一律不碰。

取原文时（export）再把这段文本**原样**发回去，客户端直接丢给
WorldExportImportService 解析。所以服务端不解析、不校验词条结构 —— 词条字段
（正则激活键、递归扫描、冷却/延迟轮数、装饰符、排序……）还会继续长，服务端
一旦去解析并校验 schema，每加一个字段都要改后端 + 迁移，那才是真正的限制。

唯一的「格式要求」是它必须是合法 JSON —— 这是「存为 JSON」本身的前提，
不是我们额外加的限制。

## 复制统计

取原文就是「复制世界卡」这个动作本身：那一次请求同时记一条 WorldCopyStat，
口径与 CardCopyStat 完全一致（同一用户对同一个世界每天最多 +1，明细每次都记）。
"""


from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import WorldCard, WorldCopyStat
from ..services.world_service import (
    can_view as _can_view,
)
from ..services.world_service import (
    entry_index,
    favorite_state,
    popular_tags,
    publish_world,
    user_worlds_query,
    world_list_query,
)
from ..services.world_service import (
    preview as _preview,
)
from ..services.world_service import (
    tags_map as _tags_map,
)
from ..utils import get_user_by_username
from .api import (
    _ensure_self,
    _soft_auth,
    _user_public,
    api_login_required,
    err,
    ok,
    paginated,
)

worlds_bp = Blueprint("worlds_api", __name__, url_prefix="/api/v1")

# 网页版蓝图。与 App 的 API 共用同一批查询/预览/统计函数 —— 两端口径不该分叉。
worlds_web = Blueprint("worlds", __name__, url_prefix="/worlds")

# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _world_light(world, tags=None):
    """列表项：只有元信息，**绝不带 payload**。"""
    return {
        "id": world.id,
        "name": world.name,
        "summary": world.summary or "",
        "entry_count": world.entry_count or 0,
        "payload_bytes": world.payload_bytes or 0,
        "copy_count": world.copy_count or 0,
        "view_count": world.view_count or 0,
        # 审核状态与隐藏状态：作者要能一眼看出自己的条目是「审核中 / 已拒绝 /
        # 已下架」，否则「我的世界卡」里三种状态长得一模一样（角色卡那边一直
        # 是给 status + is_hidden 的，这里对齐）。
        "status": world.status,
        "is_hidden": bool(world.is_hidden),
        "tags": tags if tags is not None else [],
        "author": _user_public(world.author) if world.author else None,
        "created_at": world.created_at.isoformat() if world.created_at else None,
        # 更新时间：世界书是会改的，只给创建时间看不出「这本是不是还在维护」。
        "updated_at": world.updated_at.isoformat() if world.updated_at else None,
    }


def _viewer():
    """当前访问者。

    用 _ensure_self 而不是只看 g.api_user：App 走 Bearer token（g.api_user 有值），
    网页/带 cookie 的调试请求走 flask_login session（只有 current_user 有值）。
    早先这里只读 g.api_user，于是「网页登录后调 API」会被当成匿名 —— 自己待审核
    的世界卡看不到、已收藏状态也不返回。未登录时返回 AnonymousUser，各处的
    is_authenticated 判定会把它当匿名，行为不变。
    """
    return _ensure_self()


def _page_arg(name, default):
    value = request.args.get(name, type=int)
    return value if value and value > 0 else default


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------


@worlds_bp.route("/worlds", methods=["GET"])
def worlds_list():
    """世界卡列表。

    参数：q（搜名称/简介）、tag（按标签）、sort（new | copies）、page、per_page。
    """
    _soft_auth()
    # 查询构造收拢在 services/world_service：网页版用的是同一个函数，免得两端
    # 的筛选/排序各写一遍然后慢慢漂移。
    query = world_list_query(
        _viewer(),
        keyword=request.args.get("q") or "",
        tag=request.args.get("tag") or "",
        sort=request.args.get("sort") or "",
    )

    page = _page_arg("page", 1)
    per_page = min(_page_arg("per_page", 20), 50)
    pag = query.paginate(page=page, per_page=per_page, error_out=False)
    tags = _tags_map([w.id for w in pag.items])
    return paginated(
        query,
        page=page,
        per_page=per_page,
        serialize_fn=lambda w: _world_light(w, tags.get(w.id, [])),
    )


@worlds_bp.route("/worlds/<world_id>", methods=["GET"])
def worlds_detail(world_id):
    """世界卡详情：元信息 + 词条名预览 + 1 条示例，**不含全量原文**。

    只给词条名 + 一条示例是刻意的：全展示就没人愿意发布（别人抄走就行），
    全不展示就没人愿意导入（不知道里面写了什么）。
    """
    _soft_auth()
    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, _viewer()):
        return err("世界卡不存在", 404)

    world.view_count = (world.view_count or 0) + 1
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()

    names, sample = _preview(world)
    data = _world_light(world, _tags_map([world.id]).get(world.id, []))
    data["entry_names"] = names
    # 词条索引（名字 + 人物/名词 + 触发词）：App 详情页要和网页版一样能
    # 「按类型分组 + 按名字/触发词搜索」。只给 entry_names 的话客户端只能
    # 平铺一屏名字，几百条的世界书完全没法看。
    # **仍然不含 description**（只有那一条示例有），取舍与网页版一致。
    data["entry_index"] = entry_index(world)
    data["sample_entry"] = sample
    favorited, favorite_count = favorite_state(world.id, _viewer())
    data["favorited"] = favorited
    data["favorite_count"] = favorite_count
    return ok(data)


@worlds_bp.route("/worlds/tags", methods=["GET"])
def worlds_tags():
    """热门标签（列表页筛选条用）。

    与 /cards/meta 同一个思路：客户端不该为了做筛选条去猜标签，也不该把
    全量世界卡拉下来自己聚合。
    """
    _soft_auth()
    return ok({"tags": popular_tags(_viewer(), limit=24)})


@worlds_bp.route("/users/<username>/worlds", methods=["GET"])
def users_worlds(username):
    """某个用户发布的世界卡（作者主页的「世界」tab）。

    可见性与 /users/<username> 同一口径：被禁用的主页返回空列表而不是 404
    —— 客户端把「主页被封」渲染成「网络错误」是更糟的体验。
    """
    _soft_auth()
    u = get_user_by_username(username)
    if u is None:
        return err("用户不存在", 404)
    viewer = _ensure_self()
    is_self = viewer.is_authenticated and viewer.id == u.id
    is_admin = viewer.is_authenticated and getattr(viewer, "is_super_admin", False)
    if (not is_self) and (not is_admin) and u.is_profile_banned:
        return ok({"items": [], "page": 1, "pages": 0, "total": 0, "has_next": False})

    query = user_worlds_query(viewer, u.id)
    page = _page_arg("page", 1)
    per_page = min(_page_arg("per_page", 12), 50)
    pag = query.paginate(page=page, per_page=per_page, error_out=False)
    tags = _tags_map([w.id for w in pag.items])
    return paginated(
        query,
        page=page,
        per_page=per_page,
        serialize_fn=lambda w: _world_light(w, tags.get(w.id, [])),
    )


# ---------------------------------------------------------------------------
# 取原文（= 复制世界卡，同时统计）
# ---------------------------------------------------------------------------


@worlds_bp.route("/worlds/<world_id>/export", methods=["GET"])
@api_login_required
def worlds_export(world_id):
    """取世界卡原文。

    **直接把存进去的那段文本原样吐回去**，不套 {"ok": ...} 信封 —— 套信封要把
    几百 KB 的文本再转义一遍，客户端还得多解析一次。客户端拿到 body 直接交给
    WorldExportImportService 即可。

    这一次请求就是「复制世界卡」本身，所以同时记一条复制统计。
    """
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, viewer):
        return err("世界卡不存在", 404)

    _record_copy(world, viewer)

    return Response(
        world.payload or "{}",
        mimetype="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="world.json"',
            # 复制量可能刚刚 +1，让客户端拿到最新值。
            "X-World-Copy-Count": str(world.copy_count or 0),
        },
    )


def _record_copy(world, viewer):
    """复制统计：失败不影响取原文（与 CardCopyStat 的语义一致）。"""
    try:
        copied_today = WorldCopyStat.query.filter(
            WorldCopyStat.user_id == viewer.id,
            WorldCopyStat.world_id == world.id,
            db.func.date(WorldCopyStat.copied_at) == db.func.date(db.func.now()),
        ).first()
        db.session.add(
            WorldCopyStat(
                world_id=world.id,
                world_name=world.name,
                user_id=viewer.id,
                username=viewer.username,
                copier_ip=(request.headers.get("X-Forwarded-For", "") or request.remote_addr or "")
                .split(",")[0]
                .strip()
                or None,
            )
        )
        if copied_today is None:
            world.copy_count = (world.copy_count or 0) + 1
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        from flask import current_app

        current_app.logger.warning("记录世界卡复制统计失败: %s", exc)


# ---------------------------------------------------------------------------
# 发布 / 撤回
# ---------------------------------------------------------------------------


@worlds_bp.route("/worlds/<world_id>/favorite", methods=["POST"])
@api_login_required
def worlds_favorite(world_id):
    """收藏 / 取消收藏（toggle）。返回最新状态与收藏数。"""
    from ..models import WorldFavorite

    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, viewer):
        return err("世界卡不存在", 404)

    existing = db.session.get(WorldFavorite, (viewer.id, world.id))
    if existing is None:
        db.session.add(WorldFavorite(user_id=viewer.id, world_id=world.id))
        favorited = True
    else:
        db.session.delete(existing)
        favorited = False
    db.session.commit()
    count = WorldFavorite.query.filter_by(world_id=world.id).count()
    return ok({"favorited": favorited, "count": count})


@worlds_bp.route("/my/worlds", methods=["GET"])
@api_login_required
def my_worlds():
    """我发布的世界卡。

    **不套 visible_to**：作者要能看到自己的待审核 / 已拒绝 / 已下架条目（客户端
    要据此显示状态徽章），visible_to 对本人会过滤掉 is_hidden 的。
    """
    viewer = _ensure_self()
    page = _page_arg("page", 1)
    query = WorldCard.query.filter_by(author_id=viewer.id).order_by(
        WorldCard.created_at.desc()
    )
    pag = query.paginate(page=page, per_page=20, error_out=False)
    tags = _tags_map([w.id for w in pag.items])
    return paginated(
        query,
        page=page,
        per_page=20,
        serialize_fn=lambda w: _world_light(w, tags.get(w.id, [])),
    )


@worlds_bp.route("/my/world-favorites", methods=["GET"])
@api_login_required
def my_world_favorites():
    """我收藏的世界卡（分页）。"""
    from ..models import WorldFavorite

    viewer = _ensure_self()
    page = _page_arg("page", 1)
    query = (
        WorldCard.visible_to(viewer)
        .join(WorldFavorite, WorldFavorite.world_id == WorldCard.id)
        .filter(WorldFavorite.user_id == viewer.id)
        .order_by(WorldFavorite.created_at.desc())
    )
    pag = query.paginate(page=page, per_page=20, error_out=False)
    tags = _tags_map([w.id for w in pag.items])
    return paginated(
        query,
        page=page,
        per_page=20,
        serialize_fn=lambda w: _world_light(w, tags.get(w.id, [])),
    )


@worlds_bp.route("/worlds", methods=["POST"])
@api_login_required
def worlds_publish():
    """发布世界卡。

    **请求体就是世界 JSON 原文本身**（Content-Type: application/json）。
    服务端只读顶层字段填元信息，原文按收到的字节原样入库 —— 不做
    json.dumps(json.loads(...)) 这种往返，那会改掉键序、空格和数字格式。
    """
    world, error, reused = publish_world(_ensure_self(), request.get_data(as_text=True) or "")
    if error is not None:
        return err(error, 400)
    return ok({"id": world.id, "status": world.status, "reused": reused})


@worlds_bp.route("/worlds/<world_id>", methods=["DELETE"])
@api_login_required
def worlds_delete(world_id):
    """作者撤回自己的世界卡。

    软删除（is_hidden = True）：world_copy_stats 有外键指向它，硬删会连带
    丢失版权溯源记录。
    """
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != viewer.id:
        return err("世界卡不存在", 404)
    world.is_hidden = True
    world.status = "deleted"
    db.session.commit()
    return ok({"id": world.id})


# ---------------------------------------------------------------------------
# 网页版（列表 / 详情 / 下载）
# ---------------------------------------------------------------------------


@worlds_web.route("/")
def index():
    """世界卡列表页。

    与 App 一致：**列表不展示正文**。世界书动辄几百 KB，列表页带上它就没法
    翻页了；想看内容去详情页（那里给词条名 + 一条示例）。
    """
    page = _page_arg("page", 1)
    sort = (request.args.get("sort") or "new").strip()
    keyword = (request.args.get("q") or "").strip()

    # 与 App 的 /api/v1/worlds 共用同一个查询构造函数 —— 网页版此前**漏了 tag
    # 筛选**，就是"两份代码各自演化"的直接后果。
    query = world_list_query(
        current_user,
        keyword=keyword,
        tag=request.args.get("tag") or "",
        sort=sort,
    )

    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "worlds/index.html",
        pagination=pagination,
        worlds=pagination.items,
        tags=_tags_map([w.id for w in pagination.items]),
        q=keyword,
        sort=sort,
    )


@worlds_web.route("/<world_id>")
def detail(world_id):
    """世界卡详情页：元信息 + 词条名 + 1 条示例。"""
    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, current_user):
        abort(404)

    world.view_count = (world.view_count or 0) + 1
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()

    names, sample = _preview(world)
    # 详情页要「分组 + 折叠 + 搜索」，所以额外给一份带 type 的索引。
    # API 那边仍然只给 entry_names（客户端契约不变）。
    index = entry_index(world)
    groups = [
        ("person", "人物", [e for e in index if e["type"] == "person"]),
        ("noun", "名词 / 设定", [e for e in index if e["type"] == "noun"]),
    ]
    favorited, favorite_count = favorite_state(world.id, current_user)
    return render_template(
        "worlds/show.html",
        world=world,
        favorited=favorited,
        favorite_count=favorite_count,
        tags=_tags_map([world.id]).get(world.id, []),
        entry_names=names,
        entry_groups=[g for g in groups if g[2]],
        entry_shown=len(index),
        sample_entry=sample,
    )


@worlds_web.route("/<world_id>/download")
@login_required
def download(world_id):
    """下载原文（网页版的「复制」）。

    与 App 的 export 同一口径：**原样返回存进去的文本**，同时记一次复制统计。
    """
    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, current_user):
        abort(404)
    _record_copy(world, current_user)
    return Response(
        world.payload or "{}",
        mimetype="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="world.json"',
            "X-World-Copy-Count": str(world.copy_count or 0),
        },
    )


@worlds_web.route("/<world_id>/favorite", methods=["POST"])
@login_required
def favorite(world_id):
    """收藏 / 取消收藏（网页版的表单 POST，跳回详情页）。"""
    from ..models import WorldFavorite

    world = db.session.get(WorldCard, world_id)
    if world is None or not _can_view(world, current_user):
        abort(404)

    existing = db.session.get(WorldFavorite, (current_user.id, world.id))
    if existing is None:
        db.session.add(WorldFavorite(user_id=current_user.id, world_id=world.id))
        flash("已收藏，可在「我的收藏」里找到", "success")
    else:
        db.session.delete(existing)
        flash("已取消收藏", "success")
    db.session.commit()
    return redirect(url_for("worlds.detail", world_id=world.id))


@worlds_web.route("/publish", methods=["GET", "POST"])
@login_required
def publish():
    """网页版发布入口：粘贴世界 JSON 原文，或上传导出的文件。

    与 App 的 POST /api/v1/worlds **共用 publish_world 一个函数** —— 原文原样
    入库、内容指纹幂等、发布后建索引，两端口径完全一致。
    """
    raw = ""
    if request.method == "POST":
        raw = (request.form.get("payload") or "").strip()
        upload = request.files.get("file")
        if upload is not None and upload.filename:
            try:
                raw = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                flash("文件不是 UTF-8 文本，无法解析", "danger")
                return render_template("worlds/publish.html", payload=raw)

        world, error, reused = publish_world(current_user, raw)
        if error is not None:
            flash(error, "danger")
            return render_template("worlds/publish.html", payload=raw)
        if reused:
            flash("这个内容之前已经提交过，正在审核中", "warning")
        else:
            flash("已提交，等待审核", "success")
        return redirect(url_for("worlds.detail", world_id=world.id))

    return render_template("worlds/publish.html", payload=raw)


@worlds_web.route("/favorites")
@login_required
def favorites():
    """我收藏的世界卡。

    路径是静态的 /worlds/favorites —— 与 /worlds/<world_id> 不冲突：
    Werkzeug 的路由里静态规则优先于带转换器的规则，而且世界卡 id 是 UUID，
    不可能真的叫 favorites。
    """
    from ..models import WorldFavorite

    page = _page_arg("page", 1)
    query = (
        WorldCard.visible_to(current_user)
        .join(WorldFavorite, WorldFavorite.world_id == WorldCard.id)
        .filter(WorldFavorite.user_id == current_user.id)
        .order_by(WorldFavorite.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "worlds/favorites.html",
        pagination=pagination,
        worlds=pagination.items,
        tags=_tags_map([w.id for w in pagination.items]),
    )
