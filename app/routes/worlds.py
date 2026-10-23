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


import json
import re
import uuid

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
from ..services.world_import_service import parse_imported_world
from ..services.world_service import (
    can_view as _can_view,
)
from ..services.world_service import (
    entry_index,
    favorite_state,
    popular_tags,
    preview_names,
    publish_world,
    resubmit_world,
    set_hidden,
    update_world,
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
        # 卡片上的词条名预览（发布/编辑时算好的快照，不解析 payload）。
        "preview_names": preview_names(world),
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


@worlds_bp.route("/worlds/<world_id>", methods=["PUT"])
@api_login_required
def worlds_update(world_id):
    """作者编辑自己的世界卡。

    请求体与发布一致：**世界 JSON 原文**。编辑语义是「整份世界书重写」而不是
    「改几个字段」—— 编辑器给的是完整世界观，词条可能被增删改。

    替换后状态回到 pending：审核过的是旧内容，新内容没人看过。
    """
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != viewer.id:
        return err("世界卡不存在", 404)

    world, error = update_world(world, request.get_data(as_text=True) or "")
    if error is not None:
        return err(error, 400)
    return ok({"id": world.id, "status": world.status})


@worlds_bp.route("/worlds/<world_id>/source", methods=["GET"])
@api_login_required
def worlds_source(world_id):
    """作者取自己的世界卡原文，**用于编辑，不记复制统计**。

    不能用 /export：那是「复制」动作，会 +1 复制量并写一条溯源记录。作者改自己的
    东西不该被算成别人复制了一次。
    """
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != viewer.id:
        return err("世界卡不存在", 404)
    return Response(
        world.payload or "{}",
        mimetype="application/json",
    )


@worlds_bp.route("/worlds/<world_id>/hide", methods=["POST"])
@api_login_required
def worlds_hide(world_id):
    """作者下架 / 恢复自己的世界卡（toggle，不动审核状态）。"""
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != viewer.id:
        return err("世界卡不存在", 404)
    world = set_hidden(world, not world.is_hidden)
    return ok({"id": world.id, "hidden": bool(world.is_hidden)})


@worlds_bp.route("/worlds/<world_id>/resubmit", methods=["POST"])
@api_login_required
def worlds_resubmit(world_id):
    """被拒绝 / 已撤回的世界卡重新提审。"""
    viewer = _ensure_self()
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != viewer.id:
        return err("世界卡不存在", 404)
    if world.status not in ("rejected", "deleted"):
        return err("只有被拒绝或已撤回的世界卡需要重新提审", 400)
    world = resubmit_world(world)
    return ok({"id": world.id, "status": world.status})


# ---------------------------------------------------------------------------
# 网页版（列表 / 详情 / 下载 / 发布 / 编辑）
# ---------------------------------------------------------------------------


@worlds_web.route("/")
def index():
    """世界卡列表页：探索页形态（排序 + 标签筛选 + 卡片网格）。

    **不设搜索框** —— 全站搜索（/search?type=world）才是统一入口；栏目里再放
    一个搜索框，就等于「找世界书」有两个入口，两边还各自演化。

    与 App 一致：列表不展示正文。世界书动辄几百 KB，列表页带上它就没法翻页。
    """
    page = _page_arg("page", 1)
    sort = (request.args.get("sort") or "new").strip()
    tag = (request.args.get("tag") or "").strip()

    # 与 App 的 /api/v1/worlds 共用同一个查询构造函数。
    query = world_list_query(current_user, tag=tag, sort=sort)

    pagination = query.paginate(page=page, per_page=24, error_out=False)
    return render_template(
        "worlds/index.html",
        pagination=pagination,
        worlds=pagination.items,
        tags=_tags_map([w.id for w in pagination.items]),
        hot_tags=popular_tags(current_user, limit=24),
        tag=tag,
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


# ---------------------------------------------------------------------------
# 网页版编辑器：表单 <-> 世界 JSON
#
# 网页端的发布/编辑用的是**完整世界观编辑器**（名称/背景/简介/标签/禁词 +
# 词条列表，含 keyRegex、递归、冷却、延迟、装饰符）。表单提交上来的是普通
# 字段 + 一段 entries_json，这里负责拼回客户端认识的 World JSON。
#
# 关键：**顶层必须带 id**。客户端 World.fromJson 把 id 当非空字符串读，缺了
# 直接崩 —— 别人下载这份世界再导入时会炸，所以这里必须给一个 UUID。
# ---------------------------------------------------------------------------


def _split_list(raw):
    """逗号 / 中文逗号 / 换行分隔的标签或禁词。"""
    return [t.strip() for t in re.split(r"[,，\n]", raw or "") if t.strip()]


def _empty_state():
    """空白的编辑器 state。"""
    return {
        "id": "",
        "name": "",
        "summary": "",
        "description": "",
        "tags": "",
        "forbidden_words": "",
        "entries": [],
    }


def _import_state(req):
    """从「导入」表单里取内容并解析。返回 (state, error)。

    支持两种投喂方式：上传 .dnaworld / .json 文件，或直接把 JSON 粘进文本框。
    """
    raw = ""
    upload = req.files.get("file")
    if upload is not None and upload.filename:
        try:
            raw = upload.read().decode("utf-8")
        except UnicodeDecodeError:
            return None, "文件不是 UTF-8 文本，无法解析"
    if not raw.strip():
        raw = req.form.get("payload") or ""
    return parse_imported_world(raw)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state_from_request():
    """把表单读成一个可回填的 state（出错时原样还给模板，不丢用户输入）。"""
    entries = []
    raw_entries = request.form.get("entries_json") or "[]"
    try:
        parsed = json.loads(raw_entries)
        if isinstance(parsed, list):
            entries = [e for e in parsed if isinstance(e, dict)]
    except ValueError:
        entries = []
    return {
        "id": (request.form.get("world_id") or "").strip(),
        "name": (request.form.get("name") or "").strip(),
        "summary": (request.form.get("summary") or "").strip(),
        "description": (request.form.get("description") or "").strip(),
        "tags": request.form.get("tags") or "",
        "forbidden_words": request.form.get("forbidden_words") or "",
        "entries": entries,
    }


def _state_from_world(world):
    """把已发布的世界读回成 state（编辑页回填）。"""
    try:
        data = json.loads(world.payload or "{}")
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    entries = data.get("entries")
    tags = _tags_map([world.id]).get(world.id, [])
    return {
        "id": world.id,
        "name": data.get("name") or world.name or "",
        "summary": data.get("summary") or world.summary or "",
        "description": data.get("description") or "",
        "tags": ", ".join(tags),
        "forbidden_words": ", ".join(
            [w for w in (data.get("forbiddenWords") or []) if isinstance(w, str)]
        ),
        "entries": [e for e in entries if isinstance(e, dict)]
        if isinstance(entries, list)
        else [],
    }


def _validate_state(state):
    """表单校验。返回错误文案；通过返回 None。

    与本体编辑器一致：**世界必须有名字，词条也必须有名字**。本体那边是弹窗直接
    拦住；网页这边如果放过去，会得到一条「有正文但没名字」的词条 —— 它既不会
    出现在详情页的词条目录里，也永远触发不了（没有主触发词），属于静默坏数据。
    """
    if not (state.get("name") or "").strip():
        return "世界需要一个名称"
    for idx, entry in enumerate(state.get("entries") or [], 1):
        name = entry.get("name")
        name = name.strip() if isinstance(name, str) else ""
        desc = entry.get("description")
        desc = desc if isinstance(desc, str) else ""
        if not name and desc.strip():
            return f"第 {idx} 条词条没有名称（有正文的词条必须有名字，否则永远触发不了）"
    return None


def _state_to_payload(state):
    """state -> 世界 JSON 原文（与客户端 World.toJson 同构）。"""
    clean = []
    for item in state["entries"]:
        name = item.get("name")
        name = name.strip() if isinstance(name, str) else ""
        description = item.get("description")
        description = description if isinstance(description, str) else ""
        # 空词条（名字和正文都空）直接丢，别让它占一个位置。
        if not name and not description.strip():
            continue
        keys = item.get("keys")
        is_person = item.get("type") == "person"

        # 人物专属字段只在人物词条上保留（与本体编辑器一致：切回名词就清空）。
        gender = item.get("gender") if is_person else None
        if gender not in ("male", "female", "other"):
            gender = None
        age = item.get("age") if is_person else None
        if not (isinstance(age, str) and age.strip()):
            age = None
        status = item.get("status") if is_person else None
        if status not in ("normal", "dead"):
            status = None
        relation = None
        if is_person:
            target_id = item.get("relationTarget")
            rel_content = item.get("relationContent")
            if (
                isinstance(target_id, str)
                and target_id
                and isinstance(rel_content, str)
                and rel_content.strip()
            ):
                relation = {"targetId": target_id, "content": rel_content.strip()}

        clean.append(
            {
                "id": (item.get("id") or str(uuid.uuid4())),
                "name": name,
                "description": description,
                "type": "person" if is_person else "noun",
                "gender": gender,
                "age": age,
                "status": status,
                "relation": relation,
                "keys": (
                    [k for k in keys if isinstance(k, str) and k.strip()]
                    if isinstance(keys, list)
                    else []
                ),
                "keyRegex": (item.get("keyRegex") or None),
                "caseSensitive": bool(item.get("caseSensitive")),
                "matchWholeWords": bool(item.get("matchWholeWords")),
                "recursive": bool(item.get("recursive")),
                # 与本体一致：轮数收在 0–100，避免手滑输入把世界书弄成永不触发。
                "order": max(0, _as_int(item.get("order"))),
                "cooldownRounds": max(0, min(100, _as_int(item.get("cooldownRounds")))),
                "delayRounds": max(0, min(100, _as_int(item.get("delayRounds")))),
                "decorator": item.get("decorator") or None,
            }
        )

    data = {
        "id": state["id"] or str(uuid.uuid4()),
        "name": state["name"],
        "summary": state["summary"],
        "description": state["description"],
        "tags": _split_list(state["tags"]),
        "forbiddenWords": _split_list(state["forbidden_words"]),
        "entries": clean,
        "archived": False,
    }
    return json.dumps(data, ensure_ascii=False)


def _export_response(state):
    """把当前表单导出成 .dnaworld 附件（与客户端 exportWorld 同一套信封）。

    网页端原来没有「导出」：写了一半的世界只能先发布、再从详情页下载。这里让
    编辑器直接吐一份文件，三端对齐。
    """
    from datetime import UTC, datetime
    from urllib.parse import quote

    payload = json.loads(_state_to_payload(state))
    package = {
        "version": 1,
        "exportType": "world",
        "exportedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "world": payload,
    }
    text = json.dumps(package, ensure_ascii=False, indent=2)

    raw_name = (state.get("name") or "world").strip() or "world"
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", raw_name)[:60] or "world"
    return Response(
        text,
        mimetype="application/json",
        headers={
            # ASCII 兜底 + RFC 5987 的中文名，浏览器都能落对文件名。
            "Content-Disposition": (
                'attachment; filename="world.dnaworld"; '
                f"filename*=UTF-8''{quote(safe + '.dnaworld')}"
            )
        },
    )


def _render_editor(mode, world, state, form_action):
    """渲染编辑器。

    热门标签是**社区**的东西，由服务端在这里取好交给模板 —— 编辑器模板自己
    不该去猜，更不该让别的端（比如本体）为了候选去请求社区接口。
    """
    return render_template(
        "worlds/editor.html",
        mode=mode,
        world=world,
        state=state,
        form_action=form_action,
        hot_tags=[t["tag"] for t in popular_tags(current_user, limit=12)],
    )


@worlds_web.route("/publish", methods=["GET", "POST"])
@login_required
def publish():
    """网页版发布：完整世界观编辑器（不是贴 JSON 的文本框）。

    与 App 的 POST /api/v1/worlds **共用 publish_world 一个函数** —— 原文原样
    入库、内容指纹幂等、发布后建索引，两端口径完全一致。
    """
    publish_action = url_for("worlds.publish")

    if request.method == "POST":
        # 「导入」表单：只解析并回填编辑器，不发布。
        if (request.form.get("do") or "") == "import":
            state, error = _import_state(request)
            if error is not None:
                flash(error, "danger")
                state = _empty_state()
            else:
                flash(
                    f"已识别：{len(state['entries'])} 个词条，检查后点「保存并提交审核」",
                    "success",
                )
            return _render_editor("publish", None, state, publish_action)

        # 「导出」：把当前表单落成 .dnaworld 文件，不写库。
        if (request.form.get("do") or "") == "export":
            state = _state_from_request()
            invalid = _validate_state(state)
            if invalid is not None:
                flash(invalid, "danger")
                return _render_editor("publish", None, state, publish_action)
            return _export_response(state)

        state = _state_from_request()
        invalid = _validate_state(state)
        if invalid is not None:
            flash(invalid, "danger")
            return _render_editor("publish", None, state, publish_action)

        raw = _state_to_payload(state)
        world, error, reused = publish_world(current_user, raw)
        if error is not None:
            flash(error, "danger")
            return _render_editor("publish", None, state, publish_action)
        if reused:
            flash("这个内容之前已经提交过，正在审核中", "warning")
        else:
            flash("已提交，等待审核", "success")
        return redirect(url_for("worlds.detail", world_id=world.id))

    return _render_editor("publish", None, _empty_state(), publish_action)


@worlds_web.route("/<world_id>/edit", methods=["GET", "POST"])
@login_required
def edit(world_id):
    """作者编辑自己的世界卡：同一个编辑器，保存后重新走审核。"""
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != current_user.id:
        abort(404)
    edit_action = url_for("worlds.edit", world_id=world.id)

    if request.method == "POST":
        if (request.form.get("do") or "") == "import":
            state, error = _import_state(request)
            if error is not None:
                flash(error, "danger")
                state = _state_from_world(world)
            else:
                flash(
                    f"已识别：{len(state['entries'])} 个词条，保存后会替换当前世界并重新审核",
                    "success",
                )
            return _render_editor("edit", world, state, edit_action)

        if (request.form.get("do") or "") == "export":
            state = _state_from_request()
            invalid = _validate_state(state)
            if invalid is not None:
                flash(invalid, "danger")
                return _render_editor("edit", world, state, edit_action)
            return _export_response(state)

        state = _state_from_request()
        invalid = _validate_state(state)
        if invalid is not None:
            flash(invalid, "danger")
            return _render_editor("edit", world, state, edit_action)

        raw = _state_to_payload(state)
        world, error = update_world(world, raw)
        if error is not None:
            flash(error, "danger")
            return _render_editor("edit", world, state, edit_action)
        flash("已保存，正在等待重新审核", "success")
        return redirect(url_for("worlds.mine"))

    return _render_editor("edit", world, _state_from_world(world), edit_action)


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


@worlds_web.route("/mine")
@login_required
def mine():
    """我的世界卡：含待审核 / 已拒绝 / 已下架，带状态与操作。

    **不套 visible_to**：作者要看到自己的全部条目，visible_to 对本人会过滤掉
    已下架的。
    """
    page = _page_arg("page", 1)
    query = WorldCard.query.filter_by(author_id=current_user.id).order_by(
        WorldCard.created_at.desc()
    )
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "worlds/mine.html",
        pagination=pagination,
        worlds=pagination.items,
        tags=_tags_map([w.id for w in pagination.items]),
    )


@worlds_web.route("/<world_id>/hide", methods=["POST"])
@login_required
def hide(world_id):
    """作者下架 / 恢复（表单 POST，跳回我的世界卡）。"""
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != current_user.id:
        abort(404)
    world = set_hidden(world, not world.is_hidden)
    flash("已下架，其他人看不到了" if world.is_hidden else "已恢复", "success")
    return redirect(url_for("worlds.mine"))


@worlds_web.route("/<world_id>/resubmit", methods=["POST"])
@login_required
def resubmit(world_id):
    """被拒绝 / 已撤回的世界卡重新提审（表单 POST）。"""
    world = db.session.get(WorldCard, world_id)
    if world is None or world.author_id != current_user.id:
        abort(404)
    if world.status not in ("rejected", "deleted"):
        flash("只有被拒绝或已撤回的世界卡需要重新提审", "warning")
        return redirect(url_for("worlds.mine"))
    resubmit_world(world)
    flash("已重新提审，等待审核", "success")
    return redirect(url_for("worlds.mine"))
