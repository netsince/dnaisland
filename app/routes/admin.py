import base64
import json
import re
import secrets
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import case, func, or_, update

from ..decorators import super_admin_required
from ..extensions import db
from ..models import (
    Article,
    Card,
    CardCopyStat,
    CardDialogueStyle,
    CardImage,
    CardTag,
    Comment,
    GenerationLog,
    GenerationModel,
    KeyUsageLog,
    Notification,
    PointTransaction,
    Punishment,
    RedemptionKey,
    Report,
    SiteRecommendation,
    Sponsor,
    Sticker,
    StickerSeries,
    TeaPoll,
    TeaPollVote,
    TeaPost,
    TeaPostFavorite,
    TeaPostImage,
    TeaPostLike,
    TeaPostTopic,
    User,
)
from ..models.punishment import (
    APPEAL_ACCEPTED,
    APPEAL_PENDING,
    APPEAL_REJECTED,
    PUNISHMENT_TYPES,
)
from ..services.card_service import cascade_delete_card
from ..services.image_service import (
    compress_image,
    optimize_image_for_export,
    raw_bytes_to_webp_data_url,
)
from ..services.notification_service import notify
from ..services.report_service import describe_report_target
from ..services.site_service import get_site_config
from ..services.sticker_service import invalidate_sticker_cache
from ..utils import get_user_by_username, status_counts


def apply_mute(user_id, reason, notify_msg):
    """对指定用户施加禁言处罚，替代 comment_reject / tea_post_reject 中重复的内联逻辑。"""
    u = db.session.get(User, user_id)
    if not u or u.has_punishment("mute"):
        return
    # 禁言通过下方 Punishment 记录实现；is_muted 为只读 property，不能直接赋值（会抛 AttributeError）
    db.session.add(
        Punishment(
            user_id=u.id,
            type="mute",
            reason=reason,
            handled_by=current_user.id,
        )
    )
    db.session.commit()
    notify(u.id, notify_msg, type_="punish")
    db.session.commit()

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------- 仪表盘 / 入口 ----------------
@admin_bp.route("/")
@super_admin_required
def index():
    return redirect(url_for("admin.review"))


# ---------------- 用户管理 ----------------
@admin_bp.route("/users")
@super_admin_required
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    status = request.args.get("status", "").strip()
    verified = request.args.get("verified", "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(User.username.like(like), User.nickname.like(like), User.email.like(like))
        )
    if role:
        query = query.filter(User.role == role)
    if status:
        query = query.filter(User.status == status)
    if verified == "yes":
        query = query.filter(User.email_verified.is_(True))
    elif verified == "no":
        query = query.filter(User.email_verified.is_(False))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template(
        "admin/users.html",
        users=pagination.items,
        pagination=pagination,
        args={"q": q, "role": role, "status": status, "verified": verified},
        q=q,
        role=role,
        status=status,
        verified=verified,
    )


@admin_bp.route("/users/create", methods=["GET", "POST"])
@super_admin_required
def user_create():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        nickname = (request.form.get("nickname") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "user"
        status = request.form.get("status") or "active"
        if not (username and nickname and email and password):
            flash("请填写用户名、昵称、邮箱和密码", "danger")
            return render_template("admin/user_form.html", user=None)
        if get_user_by_username(username):
            flash("用户名已存在", "danger")
            return render_template("admin/user_form.html", user=None)
        if User.query.filter_by(email=email).first():
            flash("邮箱已存在", "danger")
            return render_template("admin/user_form.html", user=None)
        if role not in ("user", "super_admin"):
            role = "user"
        u = User(username=username, nickname=nickname, email=email, role=role, status=status)
        u.verified = request.form.get("verified") == "1"
        label = (request.form.get("verified_label") or "").strip()
        u.verified_label = label or None
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("用户已创建", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@super_admin_required
def user_edit(user_id):
    u = db.get_or_404(User, user_id)
    if request.method == "POST":
        u.username = (request.form.get("username") or "").strip() or u.username
        u.nickname = (request.form.get("nickname") or "").strip() or u.nickname
        u.email = (request.form.get("email") or "").strip() or u.email
        u.role = request.form.get("role") or u.role
        if u.role not in ("user", "super_admin"):
            u.role = "user"
        new_status = request.form.get("status") or u.status
        if new_status not in ("active", "admin_del", "user_del", "mourning"):
            new_status = u.status
        u.status = new_status
        points_raw = request.form.get("points")
        if points_raw not in (None, ""):
            try:
                new_points = int(points_raw)
            except ValueError:
                flash("点数必须是整数", "warning")
            else:
                if new_points != (u.points or 0):
                    delta = new_points - (u.points or 0)
                    u.points = new_points
                    db.session.add(
                        PointTransaction(
                            user_id=u.id,
                            delta=delta,
                            balance_after=new_points,
                            reason="管理员调整",
                            source="admin",
                        )
                    )
        u.verified = request.form.get("verified") == "1"
        label = (request.form.get("verified_label") or "").strip()
        u.verified_label = label or None
        new_pwd = request.form.get("password") or ""
        if new_pwd:
            u.set_password(new_pwd)
        db.session.commit()
        flash("用户已更新", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=u)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@super_admin_required
def user_delete(user_id):
    u = db.get_or_404(User, user_id)
    if u.id == current_user.id:
        flash("不能删除当前登录的账号", "danger")
        return redirect(url_for("admin.users"))
    # 软删除：保留账号与其内容，仅将状态置为 admin_del。
    # 这样作者外键（cards/teahouse/comments 等的 author_id）依旧有效，
    # 其角色卡不再对外展示，茶馆与评论显示为「已删除用户」。
    u.status = "admin_del"
    db.session.commit()
    flash("用户已删除（账号已封禁，内容保留）", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/points")
@super_admin_required
def user_points(user_id):
    u = db.get_or_404(User, user_id)
    page = request.args.get("page", 1, type=int)
    query = PointTransaction.query.filter_by(user_id=u.id).order_by(
        PointTransaction.created_at.desc()
    )
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "admin/user_points.html",
        u=u,
        balance=u.points or 0,
        pagination=pagination,
        txs=pagination.items,
    )


# ---------------- 兑换码（Key）管理 ----------------
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去歧义字符


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _generate_key_code(prefix):
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(12))
    grouped = "-".join(body[i : i + 4] for i in range(0, 12, 4))
    return f"{prefix}-{grouped}" if prefix else grouped


@admin_bp.route("/keys")
@super_admin_required
def keys_list():
    tab = request.args.get("tab", "all")
    page = request.args.get("page", 1, type=int)
    query = RedemptionKey.query
    if tab == "banned":
        # 限制列表：已禁用或已达使用上限
        query = query.filter(
            db.or_(
                RedemptionKey.active.is_(False),
                RedemptionKey.used_count >= RedemptionKey.max_uses,
            )
        )
    query = query.order_by(RedemptionKey.created_at.desc())
    pagination = query.paginate(page=page, per_page=30, error_out=False)

    usage_page = request.args.get("upage", 1, type=int)
    usage = KeyUsageLog.query.order_by(KeyUsageLog.created_at.desc()).paginate(
        page=usage_page, per_page=30, error_out=False
    )
    usage_logs = usage.items
    user_ids = [log.user_id for log in usage_logs if log.user_id]
    users_map = (
        {u.id: u.username for u in User.query.filter(User.id.in_(user_ids)).all()}
        if user_ids
        else {}
    )
    for log in usage_logs:
        log.username = users_map.get(log.user_id, f"UID{log.user_id}") if log.user_id else "—"

    return render_template(
        "admin/keys.html",
        tab=tab,
        pagination=pagination,
        keys=pagination.items,
        usage=usage_logs,
        usage_pagination=usage,
    )


@admin_bp.route("/keys/generate", methods=["POST"])
@super_admin_required
def keys_generate():
    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(count, 500))
    try:
        points = int(request.form.get("points", 0))
    except ValueError:
        points = 0
    try:
        max_uses = int(request.form.get("max_uses", 1))
    except ValueError:
        max_uses = 1
    try:
        per_user_limit = int(request.form.get("per_user_limit", 1))
    except ValueError:
        per_user_limit = 1
    prefix = (request.form.get("prefix") or "").strip().upper()
    batch = (request.form.get("batch") or "").strip() or None
    valid_from = _parse_date(request.form.get("valid_from"))
    valid_to = _parse_date(request.form.get("valid_to"))

    generated = []
    for _ in range(count):
        while True:
            code = _generate_key_code(prefix)
            if not RedemptionKey.query.filter_by(code=code).first():
                break
        db.session.add(
            RedemptionKey(
                code=code,
                points=points,
                max_uses=max_uses,
                per_user_limit=per_user_limit,
                valid_from=valid_from,
                valid_to=valid_to,
                batch=batch,
                created_by=current_user.id,
            )
        )
        generated.append(code)
    db.session.commit()
    flash(f"已生成 {len(generated)} 个兑换码", "success")
    # 浏览器直接下载 txt，一行一个
    content = "\n".join(generated) + "\n"
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=redeem_keys.txt"},
    )


@admin_bp.route("/keys/<int:key_id>/toggle", methods=["POST"])
@super_admin_required
def key_toggle(key_id):
    key = db.get_or_404(RedemptionKey, key_id)
    key.active = not key.active
    db.session.commit()
    flash("兑换码状态已更新", "success")
    return redirect(url_for("admin.keys_list", tab=request.args.get("tab", "all")))


# ---------------- 生图模型管理 ----------------
@admin_bp.route("/image-models", methods=["GET", "POST"])
@super_admin_required
def image_models():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        display_name = (request.form.get("display_name") or "").strip()
        try:
            points_per_image = int(request.form.get("points_per_image", 0))
        except ValueError:
            points_per_image = 0
        if not name or not display_name:
            flash("调用名与展示名均必填", "warning")
        elif GenerationModel.query.filter_by(name=name).first():
            flash("该调用名已存在", "warning")
        else:
            db.session.add(
                GenerationModel(
                    name=name,
                    display_name=display_name,
                    points_per_image=points_per_image,
                    enabled=request.form.get("enabled") == "1",
                )
            )
            db.session.commit()
            flash("生图模型已添加", "success")
        return redirect(url_for("admin.image_models"))

    models = GenerationModel.query.order_by(GenerationModel.created_at.desc()).all()
    return render_template("admin/image_models.html", models=models)


@admin_bp.route("/image-models/<int:model_id>/toggle", methods=["POST"])
@super_admin_required
def image_model_toggle(model_id):
    m = db.get_or_404(GenerationModel, model_id)
    m.enabled = not m.enabled
    db.session.commit()
    flash("模型状态已更新", "success")
    return redirect(url_for("admin.image_models"))


@admin_bp.route("/image-models/<int:model_id>/delete", methods=["POST"])
@super_admin_required
def image_model_delete(model_id):
    m = db.get_or_404(GenerationModel, model_id)
    db.session.delete(m)
    db.session.commit()
    flash("模型已删除", "success")
    return redirect(url_for("admin.image_models"))


# ---------------------------------------------------------------------------
# 表情包管理
# ---------------------------------------------------------------------------
@admin_bp.route("/stickers", methods=["GET", "POST"])
@super_admin_required
def stickers():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_series":
            name = (request.form.get("name") or "").strip()
            slug = (request.form.get("slug") or "").strip()
            if not name or not slug:
                flash("系列名称与标识均必填", "danger")
            elif StickerSeries.query.filter_by(slug=slug).first():
                flash("系列标识已存在", "danger")
            else:
                db.session.add(
                    StickerSeries(
                        name=name,
                        slug=slug,
                        sort_order=request.form.get("sort_order", 0, type=int),
                    )
                )
                db.session.commit()
                flash("系列已添加", "success")
        return redirect(url_for("admin.stickers"))

    series = StickerSeries.query.order_by(
        StickerSeries.sort_order, StickerSeries.id
    ).all()
    return render_template("admin/stickers.html", series=series)


@admin_bp.route("/stickers/series/<int:sid>/delete", methods=["POST"])
@super_admin_required
def sticker_series_delete(sid):
    s = db.get_or_404(StickerSeries, sid)
    db.session.delete(s)  # 级联删除其下表情
    db.session.commit()
    invalidate_sticker_cache()
    flash("系列已删除", "success")
    return redirect(url_for("admin.stickers"))


@admin_bp.route("/stickers/upload", methods=["POST"])
@super_admin_required
def sticker_upload():
    series_id = request.form.get("series_id", type=int)
    prefix = (request.form.get("code") or "").strip()
    files = request.files.getlist("image")
    if not series_id or not files:
        flash("系列与至少一张图片均必填", "danger")
        return redirect(url_for("admin.stickers"))
    if prefix and not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fff-]+", prefix):
        flash("ID前缀仅允许中文、字母、数字、下划线和连字符", "danger")
        return redirect(url_for("admin.stickers"))
    if not StickerSeries.query.get(series_id):
        flash("系列不存在", "danger")
        return redirect(url_for("admin.stickers"))

    base_sort = request.form.get("sort_order", 0, type=int)
    used = set()
    imported = 0
    skipped = 0

    def clean_stem(name):
        stem = name.rsplit(".", 1)[0]
        cleaned = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]", "_", stem)
        return cleaned.strip("_-") or "sticker"

    for idx, f in enumerate(files):
        if not f or not f.filename:
            continue
        stem = clean_stem(f.filename)
        base = (prefix + "_" + stem) if prefix else stem
        code = base
        n = 2
        while True:
            dup = code in used or Sticker.query.filter_by(code=code).first() is not None
            if not dup:
                break
            code = f"{base}_{n}"
            n += 1
        used.add(code)

        raw = f.read()
        mimetype = f.mimetype or "image/png"
        try:
            data_url = f"data:{mimetype};base64," + base64.b64encode(raw).decode()
            image_data = compress_image(data_url, max_edge=480, quality=90)
        except Exception:
            skipped += 1
            continue
        db.session.add(
            Sticker(
                code=code,
                series_id=series_id,
                image_data=image_data,
                sort_order=base_sort + idx,
            )
        )
        imported += 1

    if imported:
        db.session.commit()
        invalidate_sticker_cache()
    if imported and not skipped:
        flash(f"已上传 {imported} 个表情", "success")
    elif imported and skipped:
        flash(f"已上传 {imported} 个表情，{skipped} 个图片处理失败已跳过", "warning")
    elif skipped:
        flash(f"{skipped} 个图片处理失败", "danger")
    else:
        flash("未上传任何有效图片", "danger")
    return redirect(url_for("admin.stickers"))


@admin_bp.route("/stickers/<int:sticker_id>/delete", methods=["POST"])
@super_admin_required
def sticker_delete(sticker_id):
    st = db.get_or_404(Sticker, sticker_id)
    db.session.delete(st)
    db.session.commit()
    invalidate_sticker_cache()
    flash("表情已删除", "success")
    return redirect(url_for("admin.stickers"))


@admin_bp.route("/stickers/series/<int:sid>/edit", methods=["POST"])
@super_admin_required
def sticker_series_edit(sid):
    s = db.get_or_404(StickerSeries, sid)
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip()
    sort_order = request.form.get("sort_order", 0, type=int) or 0
    if not name or not slug:
        flash("系列名称与标识均必填", "danger")
    elif slug != s.slug and StickerSeries.query.filter_by(slug=slug).first():
        flash("系列标识已存在", "danger")
    else:
        s.name = name
        s.slug = slug
        s.sort_order = sort_order
        db.session.commit()
        flash("系列已更新", "success")
    return redirect(url_for("admin.stickers"))


@admin_bp.route("/stickers/<int:sticker_id>/edit", methods=["POST"])
@super_admin_required
def sticker_edit(sticker_id):
    st = db.get_or_404(Sticker, sticker_id)
    code = (request.form.get("code") or "").strip()
    series_id = request.form.get("series_id", type=int)
    sort_order = request.form.get("sort_order", 0, type=int) or 0
    f = request.files.get("image")
    if not code or not series_id:
        flash("表情ID与所属系列均必填", "danger")
        return redirect(url_for("admin.stickers"))
    if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fff-]+", code):
        flash("表情ID仅允许中文、字母、数字、下划线和连字符", "danger")
        return redirect(url_for("admin.stickers"))
    if not StickerSeries.query.get(series_id):
        flash("系列不存在", "danger")
        return redirect(url_for("admin.stickers"))
    if code != st.code and Sticker.query.filter_by(code=code).first():
        flash("表情ID已存在", "danger")
        return redirect(url_for("admin.stickers"))
    st.code = code
    st.series_id = series_id
    st.sort_order = sort_order
    if f and f.filename:
        raw = f.read()
        mimetype = f.mimetype or "image/png"
        try:
            data_url = f"data:{mimetype};base64," + base64.b64encode(raw).decode()
            st.image_data = compress_image(data_url, max_edge=480, quality=90)
        except Exception:
            flash("图片处理失败，请换一张", "danger")
            return redirect(url_for("admin.stickers"))
    db.session.commit()
    invalidate_sticker_cache()
    flash("表情已更新", "success")
    return redirect(url_for("admin.stickers"))


@admin_bp.route("/stickers/reorder", methods=["POST"])
@super_admin_required
def sticker_reorder():
    data = request.get_json(silent=True) or {}
    kind = data.get("type")
    ids = [int(x) for x in (data.get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        return jsonify(ok=True)
    if kind == "series":
        stmt = update(StickerSeries).where(StickerSeries.id.in_(ids)).values(
            sort_order=case({sid: i for i, sid in enumerate(ids)}, value=StickerSeries.id)
        )
    elif kind == "sticker":
        # 单条 CASE WHEN 批量更新，不 SELECT 巨大的 image_data 字段
        stmt = update(Sticker).where(Sticker.id.in_(ids)).values(
            sort_order=case({stid: i for i, stid in enumerate(ids)}, value=Sticker.id)
        )
    else:
        return jsonify(ok=False, error="unknown type"), 400
    db.session.execute(stmt)
    db.session.commit()
    return jsonify(ok=True)


@admin_bp.route("/image-logs")
@super_admin_required
def image_logs():
    page = request.args.get("page", 1, type=int)
    pagination = GenerationLog.query.order_by(
        GenerationLog.created_at.desc()
    ).paginate(page=page, per_page=20, error_out=False)
    logs = pagination.items
    user_ids = [log.user_id for log in logs]
    users_map = (
        {u.id: u.nickname for u in User.query.filter(User.id.in_(user_ids)).all()}
        if user_ids
        else {}
    )
    for log in logs:
        log.nickname = users_map.get(log.user_id, f"UID{log.user_id}")
    return render_template(
        "admin/image_logs.html", pagination=pagination, logs=logs
    )


@admin_bp.route("/copy-stats")
@super_admin_required
def copy_stats():
    """角色卡复制统计：可按角色卡名 / 复制者用户名筛选，按复制时间倒序。"""
    q = request.args.get("q", "").strip()
    query = CardCopyStat.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(CardCopyStat.card_name.like(like), CardCopyStat.username.like(like))
        )
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(CardCopyStat.copied_at.desc()).paginate(
        page=page, per_page=30, error_out=False
    )
    # 概览统计：总复制次数、去重角色卡数、去重复制用户数
    total = db.session.query(func.count(CardCopyStat.id)).scalar() or 0
    cards_copied = (
        db.session.query(func.count(db.func.distinct(CardCopyStat.card_id))).scalar()
        or 0
    )
    users_copied = (
        db.session.query(func.count(db.func.distinct(CardCopyStat.user_id))).scalar()
        or 0
    )
    return render_template(
        "admin/copy_stats.html",
        pagination=pagination,
        stats=pagination.items,
        total=total,
        cards_copied=cards_copied,
        users_copied=users_copied,
        q=q,
    )


# ---------------- 站长推荐（站长板块） ----------------
@admin_bp.route("/recommend")
@super_admin_required
def recommend():
    recs = SiteRecommendation.query.order_by(
        SiteRecommendation.sort_order, SiteRecommendation.created_at
    ).all()
    # 附带被推荐对象的简要信息，便于后台展示
    items = []
    for r in recs:
        target = None
        if r.kind == "card":
            c = db.session.get(Card, r.ref_id)
            if c:
                target = {"name": c.name, "gender": c.gender, "link": url_for("user.card_detail", card_id=c.id)}
        elif r.kind == "user":
            u = db.session.get(User, int(r.ref_id)) if str(r.ref_id).isdigit() else None
            if u:
                target = {"name": u.nickname or u.username, "gender": None, "link": url_for("user.profile", username=u.username)}
        items.append({"rec": r, "target": target})
    return render_template("admin/recommend.html", items=items)


@admin_bp.route("/recommend/add", methods=["POST"])
@super_admin_required
def recommend_add():
    kind = (request.form.get("kind") or "").strip()
    ref_id = (request.form.get("ref_id") or "").strip()
    note = (request.form.get("note") or "").strip() or None
    if note:
        note = note[:200]

    if kind not in ("card", "user") or not ref_id:
        flash("请选择推荐类型并填写 ID", "danger")
        return redirect(url_for("admin.recommend"))

    if kind == "card":
        card = db.session.get(Card, ref_id)
        # 只接受「已通过审核」且未隐藏的角色卡
        if not card or card.status != "approved" or card.is_hidden:
            flash("只能推荐已通过审核且未隐藏的角色卡", "danger")
            return redirect(url_for("admin.recommend"))
        ref_id = str(card.id)
    else:
        try:
            uid = int(ref_id)
        except (TypeError, ValueError):
            flash("用户 UID 必须是数字", "danger")
            return redirect(url_for("admin.recommend"))
        u = db.session.get(User, uid)
        # 只接受状态正常、且无生效处罚的用户（管理员亦可被推荐）
        if not u or u.status != "active":
            flash("只能推荐状态正常的用户", "danger")
            return redirect(url_for("admin.recommend"))
        if u.active_punishments:
            flash("不能推荐存在生效处罚的用户", "danger")
            return redirect(url_for("admin.recommend"))
        ref_id = str(uid)

    if SiteRecommendation.query.filter_by(kind=kind, ref_id=ref_id).first():
        flash("该内容已在推荐列表中", "warning")
        return redirect(url_for("admin.recommend"))

    max_order = db.session.query(
        db.func.coalesce(db.func.max(SiteRecommendation.sort_order), 0)
    ).scalar()
    db.session.add(
        SiteRecommendation(
            kind=kind,
            ref_id=ref_id,
            note=note,
            sort_order=(max_order + 1),
            created_by=current_user.id,
        )
    )
    db.session.commit()
    flash("已添加推荐", "success")
    return redirect(url_for("admin.recommend"))


@admin_bp.route("/recommend/<int:rec_id>/delete", methods=["POST"])
@super_admin_required
def recommend_delete(rec_id):
    rec = db.session.get(SiteRecommendation, rec_id)
    if rec:
        db.session.delete(rec)
        db.session.commit()
        flash("已移除推荐", "success")
    return redirect(url_for("admin.recommend"))


@admin_bp.route("/recommend/<int:rec_id>/edit", methods=["POST"])
@super_admin_required
def recommend_edit(rec_id):
    rec = db.session.get(SiteRecommendation, rec_id)
    if not rec:
        flash("推荐不存在", "danger")
        return redirect(url_for("admin.recommend"))

    kind = (request.form.get("kind") or rec.kind).strip()
    ref_id = (request.form.get("ref_id") or "").strip() or str(rec.ref_id)
    note = (request.form.get("note") or "").strip() or None
    if note:
        note = note[:200]

    if kind not in ("card", "user"):
        flash("推荐类型非法", "danger")
        return redirect(url_for("admin.recommend"))

    # 重新校验被推荐对象的可见性（与添加时一致）
    if kind == "card":
        card = db.session.get(Card, ref_id)
        if not card or card.status != "approved" or card.is_hidden:
            flash("只能推荐已通过审核且未隐藏的角色卡", "danger")
            return redirect(url_for("admin.recommend"))
        ref_id = str(card.id)
    else:
        try:
            uid = int(ref_id)
        except (TypeError, ValueError):
            flash("用户 UID 必须是数字", "danger")
            return redirect(url_for("admin.recommend"))
        u = db.session.get(User, uid)
        if not u or u.status != "active":
            flash("只能推荐状态正常的用户", "danger")
            return redirect(url_for("admin.recommend"))
        if u.active_punishments:
            flash("不能推荐存在生效处罚的用户", "danger")
            return redirect(url_for("admin.recommend"))
        ref_id = str(uid)

    rec.kind = kind
    rec.ref_id = ref_id
    rec.note = note
    db.session.commit()
    flash("已更新推荐", "success")
    return redirect(url_for("admin.recommend"))


@admin_bp.route("/recommend/reorder", methods=["POST"])
@super_admin_required
def recommend_reorder():
    """拖拽排序：接收排序后的推荐 ID 列表，批量更新 sort_order。"""
    data = request.get_json(silent=True) or {}
    ids = [int(x) for x in (data.get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        return jsonify(ok=True)
    stmt = update(SiteRecommendation).where(
        SiteRecommendation.id.in_(ids)
    ).values(
        sort_order=case(
            {rid: i for i, rid in enumerate(ids)}, value=SiteRecommendation.id
        )
    )
    db.session.execute(stmt)
    db.session.commit()
    return jsonify(ok=True)


@admin_bp.route("/recommend/search")
@super_admin_required
def recommend_search():
    """快速选择器：仅返回「已通过审核的角色卡」或「未被处罚的用户」，并附带展示所需字段。

    - 角色卡：正方形（或 landscape/portrait 兜底）图片、制作者、简介。
    - 用户：头像、昵称 / 用户名、拥有的角色卡数量。
    """
    kind = (request.args.get("kind") or "").strip()
    q = (request.args.get("q") or "").strip()
    if kind == "card":
        query = Card.query.filter(Card.status == "approved", Card.is_hidden.is_(False))
        if q:
            query = query.filter(Card.name.ilike(f"%{q}%"))
        rows = query.order_by(Card.view_count.desc()).limit(20).all()
        # 角色卡图片：批量取出，优先 square，其次 landscape / portrait
        card_ids = [c.id for c in rows]
        img_map = {}
        if card_ids:
            for ci in CardImage.query.filter(CardImage.card_id.in_(card_ids)).all():
                img_map.setdefault(ci.card_id, {})[ci.slot] = ci.data
        author_ids = [c.author_id for c in rows]
        author_map = (
            {u.id: u for u in User.query.filter(User.id.in_(author_ids)).all()}
            if author_ids
            else {}
        )
        results = []
        for c in rows:
            data = None
            slots = img_map.get(c.id, {})
            for slot in ("square", "landscape", "portrait"):
                if slot in slots:
                    data = slots[slot]
                    break
            author = author_map.get(c.author_id)
            results.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "gender": c.gender,
                    "image": data,
                    "author": (author.nickname or author.username) if author else None,
                    "intro": (c.intro or "").strip()[:120],
                }
            )
        return jsonify(results)
    if kind == "user":
        punished = db.session.query(Punishment.user_id).filter(Punishment.status == "active")
        # 仅排除被处罚用户；管理员（含 super_admin）亦可被推荐
        query = User.query.filter(
            User.status == "active",
            User.id.notin_(punished),
        )
        if q:
            query = query.filter(
                or_(User.nickname.ilike(f"%{q}%"), User.username.ilike(f"%{q}%"))
            )
        rows = query.order_by(User.id.desc()).limit(20).all()
        # 拥有的角色卡数量（含任意状态，反映创作量）
        uid_counts = (
            dict(
                db.session.query(Card.author_id, db.func.count())
                .filter(Card.author_id.in_([u.id for u in rows]))
                .group_by(Card.author_id)
                .all()
            )
            if rows
            else {}
        )
        results = [
            {
                "id": u.id,
                "nickname": u.nickname,
                "username": u.username,
                "avatar": u.avatar,
                "card_count": uid_counts.get(u.id, 0),
            }
            for u in rows
        ]
        return jsonify(results)
    return jsonify([])


@admin_bp.route("/users/<int:user_id>/punish", methods=["GET", "POST"])
@super_admin_required
def user_punish(user_id):
    u = db.get_or_404(User, user_id)

    if request.method == "POST":
        selected = request.form.getlist("types")
        reason = (request.form.get("reason") or "").strip()
        valid = [t for t in selected if t in PUNISHMENT_TYPES]
        if not valid:
            flash("请至少选择一项处罚", "warning")
            return redirect(url_for("admin.user_punish", user_id=user_id))

        active = {p.type for p in u.active_punishments}
        applied = []
        for ptype in valid:
            if ptype in active:
                continue
            p = Punishment(
                user_id=u.id,
                type=ptype,
                reason=reason,
                handled_by=current_user.id,
            )
            db.session.add(p)
            applied.append(ptype)
        # 施加副作用（重置资料 / 清除头像）
        for ptype in valid:
            if ptype == "reset_profile":
                u.nickname = f"UID{u.id}"
                u.bio = None
                u.location = None
                u.website = None
                u.birthday = None
            elif ptype == "clear_avatar":
                u.avatar = None
        db.session.commit()

        if applied:
            summary = "、".join(PUNISHMENT_TYPES[t] for t in applied)
            notify(
                u.id,
                f"你被平台施加了以下处罚：{summary}。可在「我的处罚」中查看与申诉。",
                type_="punish",
            )
            db.session.commit()
            flash(f'已对用户"{u.username}"施加处罚：{summary}', "success")
        else:
            flash("所选处罚均已生效，未做改动", "info")
        return redirect(url_for("admin.user_punish", user_id=user_id))

    return render_template(
        "admin/user_punish.html",
        u=u,
        punishment_types=PUNISHMENT_TYPES,
        active=u.active_punishments,
    )


@admin_bp.route("/punish/<int:punishment_id>/revoke", methods=["POST"])
@super_admin_required
def punish_revoke(punishment_id):
    p = db.get_or_404(Punishment, punishment_id)
    if p.is_active:
        p.status = "revoked"
        db.session.commit()
        notify(
            p.user_id,
            f"你的一项处罚已被解除：{PUNISHMENT_TYPES.get(p.type, p.type)}。",
            type_="punish",
        )
        db.session.commit()
        flash("已解除该处罚", "success")
    return redirect(url_for("admin.user_punish", user_id=p.user_id))


@admin_bp.route("/punish/appeals")
@super_admin_required
def punish_appeals():
    items = (
        Punishment.query.filter_by(appeal_status=APPEAL_PENDING)
        .order_by(Punishment.appeal_at.asc())
        .all()
    )
    return render_template("admin/punish_appeals.html", items=items)


@admin_bp.route("/punish/<int:punishment_id>/appeal-resolve", methods=["POST"])
@super_admin_required
def punish_appeal_resolve(punishment_id):
    p = db.session.get(Punishment, punishment_id)
    if not p or p.appeal_status != APPEAL_PENDING:
        abort(404)
    action = (request.form.get("action") or "").strip()
    reply = (request.form.get("reply") or "").strip()

    if action == "accept":
        p.status = "revoked"
        p.appeal_status = APPEAL_ACCEPTED
        p.appeal_handled_at = db.func.now()
        p.appeal_handled_by = current_user.id
        p.appeal_reply = reply
        db.session.commit()
        notify(
            p.user_id,
            f"你的申诉已通过，处罚「{PUNISHMENT_TYPES.get(p.type, p.type)}」已解除。",
            type_="punish",
        )
        flash("已通过该申诉并解除处罚", "success")
    elif action == "reject":
        p.appeal_status = APPEAL_REJECTED
        p.appeal_handled_at = db.func.now()
        p.appeal_handled_by = current_user.id
        p.appeal_reply = reply
        db.session.commit()
        notify(
            p.user_id,
            "你的申诉未通过。"
            + (f"管理员回复：{reply}" if reply else ""),
            type_="punish",
        )
        flash("已驳回该申诉", "success")
    else:
        flash("无效操作", "warning")
    return redirect(url_for("admin.punish_appeals"))


# ---------------- 角色卡管理 ----------------
@admin_bp.route("/cards")
@super_admin_required
def cards():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    gender = request.args.get("gender", "").strip()
    author = request.args.get("author", "").strip()
    query = Card.query
    if q:
        query = query.filter(Card.name.like(f"%{q}%"))
    if status:
        query = query.filter(Card.status == status)
    if gender:
        query = query.filter(Card.gender == gender)
    if author:
        like = f"%{author}%"
        query = query.join(User).filter(User.username.like(like))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Card.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template(
        "admin/cards.html",
        cards=pagination.items,
        pagination=pagination,
        args={"q": q, "status": status, "gender": gender, "author": author},
        q=q,
        status=status,
        gender=gender,
        author=author,
    )


@admin_bp.route("/cards/<card_id>/edit", methods=["GET", "POST"])
@super_admin_required
def card_edit(card_id):
    card = db.get_or_404(Card, card_id)
    if request.method == "POST":
        card.name = (request.form.get("name") or "").strip() or card.name
        card.gender = request.form.get("gender") or card.gender
        card.persona = request.form.get("persona") or ""
        card.intro = request.form.get("intro") or ""
        card.opening = request.form.get("opening") or ""
        card.status = request.form.get("status") or card.status
        # 标签：中文逗号统一转为英文逗号后按逗号拆分、覆盖式更新
        raw_tags = (request.form.get("tags") or "").replace("，", ",")
        CardTag.query.filter_by(card_id=card.id).delete()
        for t in [t.strip() for t in raw_tags.split(",") if t.strip()]:
            db.session.add(CardTag(card_id=card.id, tag=t))
        # 图片：分槽位替换 / 移除 / 保留
        existing = {i.slot: i for i in CardImage.query.filter_by(card_id=card.id).all()}
        for slot in ("square", "landscape", "portrait"):
            f = request.files.get("image_" + slot)
            if f and f.filename:
                raw = f.read()
                if raw:
                    old = existing.pop(slot, None)
                    if old:
                        db.session.delete(old)
                    data_uri = raw_bytes_to_webp_data_url(raw, max_edge=1024, quality=80)
                    db.session.add(
                        CardImage(
                            card_id=card.id,
                            slot=slot,
                            data=optimize_image_for_export(data_uri),
                            optimized=True,
                        )
                    )
                    continue
            if request.form.get("image_remove_" + slot):
                old = existing.pop(slot, None)
                if old:
                    db.session.delete(old)
        db.session.commit()
        flash("角色卡已更新", "success")
        return redirect(url_for("admin.cards"))
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    images = {i.slot: i.data for i in CardImage.query.filter_by(card_id=card.id).all()}
    return render_template(
        "admin/card_form.html", card=card, tags=", ".join(tags), images=images
    )


@admin_bp.route("/cards/<card_id>/delete", methods=["POST"])
@super_admin_required
def card_delete(card_id):
    card = db.get_or_404(Card, card_id)
    cascade_delete_card(card)
    flash("角色卡已删除", "success")
    return redirect(url_for("admin.cards"))


# ---------------- 审核 ----------------
@admin_bp.route("/review")
@super_admin_required
def review():
    status = request.args.get("status", "pending").strip() or "pending"
    query = Card.query
    if status != "all":
        query = query.filter(Card.status == status)
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Card.created_at.asc()).paginate(
        page=page, per_page=20, error_out=False
    )
    # 统计各状态数量
    stats = status_counts(Card)
    return render_template(
        "admin/review_list.html",
        cards=pagination.items,
        pagination=pagination,
        args={"status": status},
        status=status,
        stats=stats,
    )


@admin_bp.route("/review/<card_id>")
@super_admin_required
def review_detail(card_id):
    card = db.get_or_404(Card, card_id)
    author = db.session.get(User, card.author_id)
    tags = [t.tag for t in CardTag.query.filter_by(card_id=card.id).all()]
    dialogue = (
        CardDialogueStyle.query.filter_by(card_id=card.id)
        .order_by(CardDialogueStyle.turn_index)
        .all()
    )
    images = {i.slot: i.data for i in CardImage.query.filter_by(card_id=card.id).all()}
    return render_template(
        "admin/review_detail.html",
        card=card,
        author=author,
        tags=tags,
        dialogue=dialogue,
        images=images,
    )


@admin_bp.route("/review/<card_id>/approve", methods=["POST"])
@super_admin_required
def review_approve(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.status == "approved":
        return redirect(url_for("admin.review"))
    card.status = "approved"
    db.session.commit()
    notify(
        card.author_id,
        f'你的角色卡"{card.name}"已通过审核并发布。',
        type_="review",
        related_card_id=card.id,
    )
    db.session.commit()
    flash(f'已通过："{card.name}"', "success")
    return redirect(url_for("admin.review"))


@admin_bp.route("/review/<card_id>/reject", methods=["POST"])
@super_admin_required
def review_reject(card_id):
    card = db.session.get(Card, card_id)
    if not card or card.status == "rejected":
        return redirect(url_for("admin.review"))
    card.status = "rejected"
    db.session.commit()
    notify(
        card.author_id,
        f'你的角色卡"{card.name}"未通过审核，可修改后重新提交。',
        type_="review",
        related_card_id=card.id,
    )
    db.session.commit()
    flash(f'已拒绝："{card.name}"', "success")
    return redirect(url_for("admin.review"))


# ---------------- 举报处理 ----------------
def _resolve_target_reports(target_type, target_id, handler_id, notice=None):
    """同一被举报对象（角色卡/评论/用户）的所有待处理举报，随本次处理一并标记完成，
    并向举报人发送通知。

    解决“一个对象被多人举报”时，管理员处理一条后其余仍显示待处理，
    且举报人无法得知处理结果的问题。
    """
    pending = Report.query.filter_by(
        target_type=target_type, target_id=str(target_id), status="pending"
    ).all()
    reporter_ids = sorted({r.reporter_id for r in pending})
    if pending:
        Report.query.filter_by(
            target_type=target_type, target_id=str(target_id), status="pending"
        ).update(
            {
                Report.status: "resolved",
                Report.handled_at: db.func.now(),
                Report.handled_by: handler_id,
            },
            synchronize_session=False,
        )
    if notice:
        for rid in reporter_ids:
            notify(rid, notice, type_="report")
    db.session.commit()
    return reporter_ids


@admin_bp.route("/reports")
@super_admin_required
def reports():
    status = request.args.get("status", "pending").strip() or "pending"
    target_type = request.args.get("type", "").strip()
    query = Report.query
    if status != "all":
        query = query.filter(Report.status == status)
    if target_type:
        query = query.filter(Report.target_type == target_type)
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Report.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    stats = status_counts(Report)
    # 统计同一被举报对象被举报的次数，用于在列表中提示“多人举报”
    rows = (
        db.session.query(Report.target_type, Report.target_id, func.count(Report.id))
        .group_by(Report.target_type, Report.target_id)
        .all()
    )
    counts = {f"{t}:{i}": c for t, i, c in rows}
    return render_template(
        "admin/reports.html",
        reports=pagination.items,
        pagination=pagination,
        args={"status": status, "type": target_type},
        status=status,
        target_type=target_type,
        stats=stats,
        counts=counts,
    )


@admin_bp.route("/reports/<int:report_id>")
@super_admin_required
def report_detail(report_id):
    r = db.get_or_404(Report, report_id)

    desc = describe_report_target(r.target_type, r.target_id)
    target_info = {
        "type": r.target_type,
        "link": desc["url"] if desc else None,
        "snippet": desc["snippet"] if desc else "（目标不存在）",
    }

    # 统计同一被举报对象被多少人举报，便于管理员判断严重程度
    related = Report.query.filter_by(
        target_type=r.target_type, target_id=r.target_id
    ).all()
    related_total = len(related)
    related_pending = sum(1 for x in related if x.status == "pending")
    related_reporters = [
        (x.reporter.nickname if x.reporter else x.reporter_id) for x in related
    ]

    return render_template(
        "admin/report_detail.html",
        report=r,
        target_info=target_info,
        related_total=related_total,
        related_pending=related_pending,
        related_reporters=related_reporters,
    )


@admin_bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@super_admin_required
def report_resolve(report_id):
    r = db.get_or_404(Report, report_id)
    _resolve_target_reports(
        r.target_type,
        r.target_id,
        current_user.id,
        notice="你举报的内容经平台审核已处理完毕，感谢你的反馈。",
    )
    flash(
        "已标记为该举报处理完毕（未采取额外措施），同一对象的其他举报也已一并处理，并已通知举报人",
        "success",
    )
    return redirect(url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/action", methods=["POST"])
@super_admin_required
def report_action(report_id):
    r = db.get_or_404(Report, report_id)
    action = (request.form.get("action") or "").strip()

    if action == "hide_card" and r.target_type == "card":
        card = db.session.get(Card, r.target_id)
        if card:
            card.is_hidden = True
    elif action == "delete_card" and r.target_type == "card":
        card = db.session.get(Card, r.target_id)
        if card:
            cascade_delete_card(card)
    elif action == "delete_comment" and r.target_type == "comment":
        comment = db.session.get(Comment, int(r.target_id))
        if comment:
            db.session.delete(comment)
    elif action == "hide_teapost" and r.target_type == "teapost":
        tp = db.session.get(TeaPost, int(r.target_id))
        if tp:
            tp.is_hidden = True
            tp.moderated = True
    elif action == "delete_teapost" and r.target_type == "teapost":
        tp = db.session.get(TeaPost, int(r.target_id))
        if tp:
            _cascade_delete_teapost(tp)
    else:
        flash("无效的处理操作", "warning")
        return redirect(url_for("admin.report_detail", report_id=report_id))

    db.session.commit()
    # 同一对象被多人举报时，本次处理一并解决所有待处理举报，并通知举报人
    notice_map = {
        "hide_card": "你举报的角色卡已被平台下架处理，感谢你的反馈。",
        "delete_card": "你举报的角色卡已被平台删除处理，感谢你的反馈。",
        "delete_comment": "你举报的评论已被平台删除处理，感谢你的反馈。",
        "hide_teapost": "你举报的茶馆帖子已被平台隐藏处理，感谢你的反馈。",
        "delete_teapost": "你举报的茶馆帖子已被平台删除处理，感谢你的反馈。",
    }
    _resolve_target_reports(
        r.target_type, r.target_id, current_user.id, notice=notice_map.get(action)
    )
    flash(
        "已对举报对象采取处理措施，同一对象的其他举报也已一并处理，并已通知举报人",
        "success",
    )
    return redirect(url_for("admin.reports"))


# ---------------- 评论审核（先发后审） ----------------
@admin_bp.route("/comments/moderation")
@super_admin_required
def comment_moderation():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Comment.query.filter_by(moderated=False)
        .order_by(Comment.created_at.desc())
        .paginate(page=page, per_page=20, error_out=False)
    )
    items = []
    for c in pagination.items:
        items.append({"comment": c, "card": db.session.get(Card, c.card_id)})
    return render_template(
        "admin/comment_moderation.html",
        items=items,
        pagination=pagination,
        args={},
        pending=Comment.query.filter_by(moderated=False).count(),
    )


@admin_bp.route("/comments/<int:comment_id>/approve", methods=["POST"])
@super_admin_required
def comment_approve(comment_id):
    c = db.get_or_404(Comment, comment_id)
    c.moderated = True  # 同意：保持可见
    db.session.commit()
    flash("已通过该评论，继续可见", "success")
    return redirect(url_for("admin.comment_moderation"))


@admin_bp.route("/comments/<int:comment_id>/reject", methods=["POST"])
@super_admin_required
def comment_reject(comment_id):
    c = db.get_or_404(Comment, comment_id)
    # 拒绝：隐藏评论 + 标记已审核
    c.is_hidden = True
    c.moderated = True
    db.session.commit()

    card = db.session.get(Card, c.card_id)
    card_name = card.name if card else "未知角色卡"
    notify(
        c.user_id,
        f'你发布在角色卡《{card_name}》下的评论因违反社区规范已被移除。',
        type_="comment",
    )

    # 可选：拒绝的同时禁言该用户（复用 mute 处罚）
    mute = request.form.get("mute") == "1"
    if mute:
        apply_mute(
            c.user_id,
            "评论被拒绝时管理员施加禁言",
            "你已被平台禁言，暂时无法发表评论。",
        )

    flash(
        "已拒绝该评论（已隐藏并通知用户）" + ("，并禁言该用户" if mute else ""),
        "success",
    )
    return redirect(url_for("admin.comment_moderation"))


# ---------------- 评论管理（总列表，独立于审核） ----------------
@admin_bp.route("/comments")
@super_admin_required
def comments():
    q = request.args.get("q", "").strip()
    author = request.args.get("author", "").strip()
    card = request.args.get("card", "").strip()
    query = Comment.query
    if q:
        query = query.filter(Comment.content.like(f"%{q}%"))
    if author:
        query = query.join(User).filter(User.username.like(f"%{author}%"))
    if card:
        query = query.join(Card).filter(Card.name.like(f"%{card}%"))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Comment.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    items = [
        {"comment": c, "card": db.session.get(Card, c.card_id)}
        for c in pagination.items
    ]
    return render_template(
        "admin/comments.html",
        items=items,
        pagination=pagination,
        args={"q": q, "author": author, "card": card},
        q=q,
        author=author,
        card=card,
    )


@admin_bp.route("/comments/<int:comment_id>/delete", methods=["POST"])
@super_admin_required
def comment_delete(comment_id):
    c = db.get_or_404(Comment, comment_id)
    db.session.delete(c)
    db.session.commit()
    flash("评论已删除", "success")
    return redirect(url_for("admin.comments"))


# ---------------- 通知发送 ----------------
NOTIFY_TEMPLATES = [
    ("感谢", "感谢你对 DNAISLAND 的贡献，期待你创作更多优质角色卡！"),
    ("欢迎", "欢迎加入 DNAISLAND！如有疑问可随时联系管理员。"),
    ("违规提醒", "你的部分内容因违反社区规范已被处理，请遵守平台规则。"),
    ("活动通知", "平台即将举办活动，敬请期待～"),
]


@admin_bp.route("/notify", methods=["GET", "POST"])
@super_admin_required
def notify_send():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        message = (request.form.get("message") or "").strip()
        if not username or not message:
            flash("请填写目标用户名与通知内容", "warning")
            return render_template("admin/notify.html", templates=NOTIFY_TEMPLATES)
        # 目标用户名支持通配符：* 匹配任意字符，转为 SQL LIKE 的 %
        if "*" in username:
            pattern = username.replace("*", "%")
            users = User.query.filter(User.username.like(pattern)).all()
        else:
            u = get_user_by_username(username)
            users = [u] if u else []
        if not users:
            flash("没有匹配的用户", "warning")
            return render_template("admin/notify.html", templates=NOTIFY_TEMPLATES)
        for u in users:
            notify(u.id, message, type_="system")
        db.session.commit()
        if len(users) == 1:
            flash(f"已向用户 {users[0].nickname}（@{users[0].username}）发送通知", "success")
        else:
            flash(f"已向 {len(users)} 名匹配用户发送通知", "success")
        return redirect(url_for("admin.notify_send"))
    return render_template("admin/notify.html", templates=NOTIFY_TEMPLATES)


# ---------------- 茶馆帖子审核（先发后审） ----------------
@admin_bp.route("/teahouse/moderation")
@super_admin_required
def tea_moderation():
    page = request.args.get("page", 1, type=int)
    pagination = (
        TeaPost.query.filter_by(moderated=False)
        .order_by(TeaPost.created_at.desc())
        .paginate(page=page, per_page=20, error_out=False)
    )
    items = []
    for p in pagination.items:
        parent = db.session.get(TeaPost, p.parent_id) if p.parent_id else None
        items.append({"post": p, "parent": parent})
    return render_template(
        "teahouse/admin_moderation.html",
        items=items,
        pagination=pagination,
        args={},
        pending=TeaPost.query.filter_by(moderated=False).count(),
    )


@admin_bp.route("/teahouse/<int:post_id>/approve", methods=["POST"])
@super_admin_required
def tea_post_approve(post_id):
    p = db.get_or_404(TeaPost, post_id)
    p.moderated = True  # 同意：保持可见
    db.session.commit()
    flash("已通过该茶馆帖子，继续可见", "success")
    return redirect(url_for("admin.tea_moderation"))


@admin_bp.route("/teahouse/<int:post_id>/reject", methods=["POST"])
@super_admin_required
def tea_post_reject(post_id):
    p = db.get_or_404(TeaPost, post_id)
    # 拒绝：隐藏帖子 + 标记已审核 + 通知作者
    p.is_hidden = True
    p.moderated = True
    db.session.commit()
    notify(p.user_id, "你在茶馆发布的帖子因违反社区规范已被移除。", type_="teahouse")

    # 可选：拒绝的同时禁言该用户（复用 mute 处罚）
    mute = request.form.get("mute") == "1"
    if mute:
        apply_mute(
            p.user_id,
            "茶馆帖子被拒绝时管理员施加禁言",
            "你已被平台禁言，暂时无法在茶馆发言。",
        )
    flash(
        "已拒绝该茶馆帖子（已隐藏并通知用户）" + ("，并禁言该用户" if mute else ""),
        "success",
    )
    return redirect(url_for("admin.tea_moderation"))


def _cascade_delete_teapost(post):
    """递归硬删帖子及其全部子孙回复，并清理点赞/收藏/话题/配图/投票/通知等关联数据，
    避免产生孤儿外键记录。

    自引用外键要求先删子后删父，因此使用递归：先处理所有子回复，再清理当前帖关联。
    """
    children = TeaPost.query.filter_by(parent_id=post.id).all()
    for child in children:
        _cascade_delete_teapost(child)

    pid = post.id
    # 投票：先删用户投票记录，再删投票聚合表与本投票
    poll = TeaPoll.query.filter_by(post_id=pid).first()
    if poll:
        TeaPollVote.query.filter_by(poll_id=poll.id).delete(synchronize_session=False)
        db.session.delete(poll)
    TeaPostImage.query.filter_by(post_id=pid).delete(synchronize_session=False)
    TeaPostLike.query.filter_by(post_id=pid).delete(synchronize_session=False)
    TeaPostFavorite.query.filter_by(post_id=pid).delete(synchronize_session=False)
    TeaPostTopic.query.filter_by(post_id=pid).delete(synchronize_session=False)
    # 指向该帖的相关通知（点赞/提及/回复等外部链接含 /teahouse/<id>）
    # 用正则精确匹配，避免误删 /teahouse/12 这类长 id 中前缀 /teahouse/1 的通知
    pattern = re.compile(rf"/teahouse/{pid}(?!\d)")
    for n in Notification.query.filter(
        Notification.message.like(f"%teahouse/{pid}%")
    ).all():
        if pattern.search(n.message):
            db.session.delete(n)
    db.session.delete(post)


# ---------------- 茶馆帖子管理（总列表，独立于审核） ----------------
@admin_bp.route("/teahouse")
@super_admin_required
def tea_posts():
    q = request.args.get("q", "").strip()
    author = request.args.get("author", "").strip()
    deleted = request.args.get("deleted", "").strip()
    query = TeaPost.query
    if q:
        query = query.filter(TeaPost.content.like(f"%{q}%"))
    if author:
        query = query.join(User).filter(User.username.like(f"%{author}%"))
    if deleted == "1":
        query = query.filter(TeaPost.is_deleted.is_(True))
    elif deleted == "0":
        query = query.filter(TeaPost.is_deleted.is_(False))
    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(TeaPost.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    # 批量统计每帖的点赞/收藏/回复数及配图/投票/话题标记，便于后台可视化这些数据
    ids = [p.id for p in pagination.items]
    like_counts = dict(
        db.session.query(TeaPostLike.post_id, db.func.count())
        .filter(TeaPostLike.post_id.in_(ids))
        .group_by(TeaPostLike.post_id)
        .all()
    )
    fav_counts = dict(
        db.session.query(TeaPostFavorite.post_id, db.func.count())
        .filter(TeaPostFavorite.post_id.in_(ids))
        .group_by(TeaPostFavorite.post_id)
        .all()
    )
    reply_counts = dict(
        db.session.query(TeaPost.parent_id, db.func.count())
        .filter(TeaPost.parent_id.in_(ids))
        .group_by(TeaPost.parent_id)
        .all()
    )
    topic_counts = dict(
        db.session.query(TeaPostTopic.post_id, db.func.count())
        .filter(TeaPostTopic.post_id.in_(ids))
        .group_by(TeaPostTopic.post_id)
        .all()
    )
    items = []
    for p in pagination.items:
        items.append(
            {
                "post": p,
                "likes": like_counts.get(p.id, 0),
                "favs": fav_counts.get(p.id, 0),
                "replies": reply_counts.get(p.id, 0),
                "topics": topic_counts.get(p.id, 0),
                "has_image": len(p.images) > 0,
                "has_poll": p.poll is not None,
            }
        )

    return render_template(
        "teahouse/admin_posts.html",
        items=items,
        pagination=pagination,
        args={"q": q, "author": author, "deleted": deleted},
        q=q,
        author=author,
        deleted=deleted,
    )


@admin_bp.route("/teahouse/<int:post_id>/hide", methods=["POST"])
@super_admin_required
def tea_post_hide(post_id):
    p = db.get_or_404(TeaPost, post_id)
    p.is_hidden = not p.is_hidden
    db.session.commit()
    flash("已切换帖子隐藏状态", "success")
    return redirect(url_for("admin.tea_posts"))


@admin_bp.route("/teahouse/<int:post_id>/delete", methods=["POST"])
@super_admin_required
def tea_post_delete(post_id):
    p = db.get_or_404(TeaPost, post_id)
    _cascade_delete_teapost(p)
    db.session.commit()
    flash("茶馆帖子及其全部回复、点赞、收藏、投票等关联数据已删除", "success")
    return redirect(url_for("admin.tea_posts"))


@admin_bp.route("/teahouse/<int:post_id>/restore", methods=["POST"])
@super_admin_required
def tea_post_restore(post_id):
    p = db.get_or_404(TeaPost, post_id)
    if not p.is_deleted:
        flash("该帖子未被删除", "warning")
        return redirect(url_for("admin.tea_posts"))
    p.is_deleted = False
    p.deleted_at = None
    db.session.commit()
    flash("已恢复该帖子", "success")
    return redirect(url_for("admin.tea_posts"))


# ---------------- 系统配置 ----------------
@admin_bp.route("/system", methods=["GET", "POST"])
@super_admin_required
def system_config():
    cfg = get_site_config()
    if request.method == "POST":
        cfg.site_name = (request.form.get("site_name") or "").strip() or "DNAISLAND"

        # 关站
        cfg.shutdown_enabled = request.form.get("shutdown_enabled") == "1"
        cfg.shutdown_message = (request.form.get("shutdown_message") or "").strip() or None

        # 公告（富文本 / HTML）
        cfg.announcement_enabled = request.form.get("announcement_enabled") == "1"
        cfg.announcement_content = (
            request.form.get("announcement_content") or ""
        ).strip() or None

        # 首页 Hero
        cfg.hero_enabled = request.form.get("hero_enabled") == "1"
        cfg.hero_title = (request.form.get("hero_title") or "").strip() or None
        cfg.hero_subtitle = (request.form.get("hero_subtitle") or "").strip() or None
        labels = request.form.getlist("hero_button_label")
        urls = request.form.getlist("hero_button_url")
        buttons = []
        for lab, u in zip(labels, urls, strict=False):
            lab = (lab or "").strip()
            u = (u or "").strip()
            if lab:
                buttons.append({"label": lab, "url": u})
        cfg.hero_buttons = json.dumps(buttons, ensure_ascii=False)

        # 协议链接（外部 URL）
        cfg.privacy_policy_url = (
            request.form.get("privacy_policy_url") or ""
        ).strip() or None
        cfg.tos_url = (request.form.get("tos_url") or "").strip() or None

        # 联系客服邮箱（mailto）
        cfg.contact_email = (request.form.get("contact_email") or "").strip() or None

        # 纪念横幅跳转 URL（mourning 状态用户主页横幅可点击跳转）
        cfg.memorial_banner_url = (
            request.form.get("memorial_banner_url") or ""
        ).strip() or None

        # 注册邮箱白名单
        cfg.email_whitelist_enabled = request.form.get("email_whitelist_enabled") == "1"
        cfg.email_whitelist_suffixes = (
            request.form.get("email_whitelist_suffixes") or ""
        ).strip() or None

        # 生图服务（OpenAI 格式通道）：仅在有值时更新，避免误清空密钥
        base_url = (request.form.get("image_base_url") or "").strip()
        if base_url:
            cfg.image_base_url = base_url
        api_key = (request.form.get("image_api_key") or "").strip()
        if api_key:
            cfg.image_api_key = api_key

        # 获取兑换码跳转地址（可选）
        cfg.redeem_code_url = (
            request.form.get("redeem_code_url") or ""
        ).strip() or None

        db.session.commit()
        flash("系统配置已保存", "success")
        return redirect(url_for("admin.system_config"))

    return render_template(
        "admin/system.html", cfg=cfg, hero_buttons=cfg.hero_buttons_list()
    )


# ---------------- 文章管理（仅管理员可发布） ----------------
@admin_bp.route("/articles")
@super_admin_required
def articles():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    try:
        query = Article.query
        if q:
            query = query.filter(Article.title.like(f"%{q}%"))
        pagination = query.order_by(Article.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        items = pagination.items
    except Exception:
        # 表尚未建立时优雅降级
        pagination = None
        items = []
    return render_template(
        "admin/articles.html",
        articles=items,
        pagination=pagination,
        q=q,
    )


def _resolve_article_cover(existing):
    """解析文章封面的上传来源，返回待存储的值（URL 字符串或 WebP base64 data URL）。

    - 勾选 remove_cover -> 清空为 None
    - 上传了图片文件 -> 转 WebP(base64 data URL) 存储
    - 填写了 URL（或遗留 base64） -> 原样或转 WebP 存储
    - 都未提供 -> 保留 existing 原值
    """
    if request.form.get("remove_cover"):
        return None
    f = request.files.get("cover_file")
    if f and f.filename:
        raw = f.read()
        if raw:
            try:
                return raw_bytes_to_webp_data_url(raw, max_edge=1024, quality=82)
            except Exception:
                flash("封面图片处理失败，请重试", "warning")
                return existing.cover if existing else None
    url = (request.form.get("cover_url") or "").strip()
    if url:
        if url.startswith("data:"):
            try:
                return compress_image(url)
            except Exception:
                return existing.cover if existing else None
        return url
    return existing.cover if existing else None


@admin_bp.route("/articles/create", methods=["GET", "POST"])
@super_admin_required
def article_create():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = request.form.get("content") or ""
        if not title or not content.strip():
            flash("请填写标题与正文", "danger")
            return render_template("admin/article_form.html", article=None)
        a = Article(
            title=title,
            summary=(request.form.get("summary") or "").strip() or None,
            content=content,
            cover=_resolve_article_cover(None),
            author_id=current_user.id,
            is_published=request.form.get("is_published") == "1",
            show_author=request.form.get("show_author") != "0",
        )
        db.session.add(a)
        db.session.commit()
        flash("文章已发布", "success")
        return redirect(url_for("admin.articles"))
    return render_template("admin/article_form.html", article=None)


@admin_bp.route("/articles/<int:article_id>/edit", methods=["GET", "POST"])
@super_admin_required
def article_edit(article_id):
    a = db.get_or_404(Article, article_id)
    if request.method == "POST":
        a.title = (request.form.get("title") or "").strip() or a.title
        a.summary = (request.form.get("summary") or "").strip() or None
        a.content = request.form.get("content") or a.content
        a.cover = _resolve_article_cover(a)
        a.is_published = request.form.get("is_published") == "1"
        a.show_author = request.form.get("show_author") != "0"
        db.session.commit()
        flash("文章已更新", "success")
        return redirect(url_for("admin.articles"))
    return render_template("admin/article_form.html", article=a)


@admin_bp.route("/articles/<int:article_id>/delete", methods=["POST"])
@super_admin_required
def article_delete(article_id):
    a = db.get_or_404(Article, article_id)
    db.session.delete(a)
    db.session.commit()
    flash("文章已删除", "success")
    return redirect(url_for("admin.articles"))


@admin_bp.route("/articles/<int:article_id>/toggle-author", methods=["POST"])
@super_admin_required
def article_toggle_author(article_id):
    """切换发布者是否公开（隐藏时显示为匿名管理员）。"""
    a = db.get_or_404(Article, article_id)
    a.show_author = not a.show_author
    db.session.commit()
    flash("已切换发布者显示状态", "success")
    return redirect(url_for("admin.articles"))


# ---------------- 赞助页面 ----------------
@admin_bp.route("/sponsors")
@super_admin_required
def sponsors():
    """赞助管理：配置（开关/标题/富文本说明/按钮链接）+ 赞助者列表。"""
    cfg = get_site_config()
    rows = Sponsor.query.order_by(Sponsor.sort_order, Sponsor.created_at).all()
    uid_map = {}
    if rows:
        uid_map = {
            u.id: u
            for u in User.query.filter(
                User.id.in_([s.user_id for s in rows])
            ).all()
        }
    items = []
    for s in rows:
        u = uid_map.get(s.user_id)
        items.append(
            {
                "sponsor": s,
                "user": (
                    {
                        "nickname": u.nickname or u.username,
                        "username": u.username,
                        "link": url_for("user.profile", username=u.username),
                    }
                    if u
                    else None
                ),
            }
        )
    return render_template("admin/sponsors.html", items=items, cfg=cfg)


@admin_bp.route("/sponsors/config", methods=["POST"])
@super_admin_required
def sponsors_config():
    """保存赞助页面配置。"""
    cfg = get_site_config()
    cfg.sponsor_enabled = request.form.get("sponsor_enabled") == "1"
    cfg.sponsor_title = (request.form.get("sponsor_title") or "").strip() or None
    cfg.sponsor_content = (
        request.form.get("sponsor_content") or ""
    ).strip() or None
    cfg.sponsor_url = (request.form.get("sponsor_url") or "").strip() or None
    db.session.commit()
    flash("赞助配置已保存", "success")
    return redirect(url_for("admin.sponsors"))


@admin_bp.route("/sponsors/add", methods=["POST"])
@super_admin_required
def sponsors_add():
    """添加赞助者：可手动粘贴 UID，也可用下方快速选择器搜索后点选。"""
    raw_uid = (request.form.get("user_id") or "").strip()
    if not raw_uid.isdigit():
        flash("请填写用户 UID 或使用搜索选择器", "danger")
        return redirect(url_for("admin.sponsors"))
    u = db.session.get(User, int(raw_uid))
    # 只接受状态正常、且无生效处罚的用户（管理员亦可加入）
    if not u or u.status != "active":
        flash("只能添加状态正常的用户", "danger")
        return redirect(url_for("admin.sponsors"))
    if u.active_punishments:
        flash("不能添加存在生效处罚的用户", "danger")
        return redirect(url_for("admin.sponsors"))
    if Sponsor.query.filter_by(user_id=u.id).first():
        flash("该用户已在赞助列表中", "warning")
        return redirect(url_for("admin.sponsors"))

    display_name = (request.form.get("display_name") or "").strip()
    amount = (request.form.get("amount") or "").strip() or None
    display_name = display_name or (u.nickname or u.username)
    max_order = db.session.query(
        db.func.coalesce(db.func.max(Sponsor.sort_order), 0)
    ).scalar()
    db.session.add(
        Sponsor(
            user_id=u.id,
            display_name=display_name[:64],
            amount=amount[:32] if amount else None,
            sort_order=(max_order + 1),
            created_by=current_user.id,
        )
    )
    db.session.commit()
    flash("已添加赞助者", "success")
    return redirect(url_for("admin.sponsors"))


@admin_bp.route("/sponsors/<int:sponsor_id>/edit", methods=["POST"])
@super_admin_required
def sponsors_edit(sponsor_id):
    """编辑赞助者显示名与累计数额（不改变绑定的用户）。"""
    s = db.session.get(Sponsor, sponsor_id)
    if not s:
        flash("赞助者不存在", "danger")
        return redirect(url_for("admin.sponsors"))
    display_name = (request.form.get("display_name") or "").strip()
    amount = (request.form.get("amount") or "").strip() or None
    fallback = (s.user.nickname or s.user.username) if s.user else s.display_name
    s.display_name = (display_name or fallback)[:64]
    s.amount = amount[:32] if amount else None
    db.session.commit()
    flash("已更新赞助者", "success")
    return redirect(url_for("admin.sponsors"))


@admin_bp.route("/sponsors/<int:sponsor_id>/delete", methods=["POST"])
@super_admin_required
def sponsors_delete(sponsor_id):
    s = db.session.get(Sponsor, sponsor_id)
    if s:
        db.session.delete(s)
        db.session.commit()
        flash("已移除赞助者", "success")
    return redirect(url_for("admin.sponsors"))


@admin_bp.route("/sponsors/reorder", methods=["POST"])
@super_admin_required
def sponsors_reorder():
    """拖拽排序：接收排序后的赞助者 ID 列表，批量更新 sort_order。"""
    data = request.get_json(silent=True) or {}
    ids = [int(x) for x in (data.get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        return jsonify(ok=True)
    stmt = update(Sponsor).where(Sponsor.id.in_(ids)).values(
        sort_order=case({rid: i for i, rid in enumerate(ids)}, value=Sponsor.id)
    )
    db.session.execute(stmt)
    db.session.commit()
    return jsonify(ok=True)


@admin_bp.route("/sponsors/search")
@super_admin_required
def sponsors_search():
    """快速选择器：仅返回状态正常、未被处罚的用户（含管理员），供添加时搜索点选。"""
    q = (request.args.get("q") or "").strip()
    punished = db.session.query(Punishment.user_id).filter(
        Punishment.status == "active"
    )
    query = User.query.filter(
        User.status == "active",
        User.id.notin_(punished),
    )
    if q:
        query = query.filter(
            or_(User.nickname.ilike(f"%{q}%"), User.username.ilike(f"%{q}%"))
        )
    rows = query.order_by(User.id.desc()).limit(20).all()
    return jsonify(
        [
            {
                "id": u.id,
                "nickname": u.nickname or u.username,
                "username": u.username,
                "avatar": u.avatar or "",
            }
            for u in rows
        ]
    )
