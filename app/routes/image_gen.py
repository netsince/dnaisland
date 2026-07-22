"""用户侧生图工作台：生图、我的记录与详情。

权限：仅登录用户；生成前校验点数，不足直接禁止。
扣费：完成后按实产张数 × 模型每图积分扣减（写入 PointTransaction）。
"""

import json

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from io import BytesIO

from ..extensions import db
from ..models import GenerationLog, GenerationModel, PointTransaction
from ..services.image_gen_service import generate_images
from ..services.image_service import (
    data_url_to_webp_bytes,
    raw_bytes_to_webp_data_url,
)
from ..services.site_service import get_site_config

image_gen_bp = Blueprint("image_gen", __name__, url_prefix="/image-gen")

# 宽高比 -> OpenAI size（照搬 infinite-canvas 默认 1k 分辨率）；auto 不传 size
ASPECT_TO_SIZE = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "4:3": "1360x1024",
    "3:4": "1024x1360",
    "16:9": "1824x1024",
    "9:16": "1024x1824",
    "auto": None,
}
VALID_ASPECTS = list(ASPECT_TO_SIZE.keys())
MAX_REFERENCES = 5
MAX_COUNT = 2


@image_gen_bp.route("/")
@login_required
def workbench():
    models = (
        GenerationModel.query.filter_by(enabled=True)
        .order_by(GenerationModel.display_name)
        .all()
    )
    page = request.args.get("page", 1, type=int)
    pagination = (
        GenerationLog.query.filter_by(user_id=current_user.id)
        .order_by(GenerationLog.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    # 默认选中每图积分最低的可用模型
    default_model = min(models, key=lambda m: m.points_per_image or 0) if models else None
    return render_template(
        "image_gen/workbench.html",
        models=models,
        default_model=default_model,
        recent=pagination.items,
        pagination=pagination,
        aspects=VALID_ASPECTS,
        max_refs=MAX_REFERENCES,
    )


def _serve_webp_from_data_url(data_url):
    """把某条生图记录的 base64 Data URL 转 WEBP 后以图片形式发送。"""
    if not data_url:
        abort(404)
    try:
        webp = data_url_to_webp_bytes(data_url)
    except Exception:
        abort(404)
    return send_file(BytesIO(webp), mimetype="image/webp")


@image_gen_bp.route("/output/<int:log_id>/<int:idx>")
@login_required
def output_image(log_id, idx):
    """产出图（原图）接口：按 log_id + 序号返回 WEBP 二进制。"""
    log = db.session.get(GenerationLog, log_id)
    if not log:
        abort(404)
    if log.user_id != current_user.id and not current_user.is_super_admin:
        abort(403)
    imgs = log.image_list()
    if idx < 0 or idx >= len(imgs):
        abort(404)
    return _serve_webp_from_data_url(imgs[idx])


@image_gen_bp.route("/reference/<int:log_id>/<int:idx>")
@login_required
def reference_image(log_id, idx):
    """参考图（垫图）接口：按 log_id + 序号返回 WEBP 二进制。"""
    log = db.session.get(GenerationLog, log_id)
    if not log:
        abort(404)
    if log.user_id != current_user.id and not current_user.is_super_admin:
        abort(403)
    refs = log.reference_image_list()
    if idx < 0 or idx >= len(refs):
        abort(404)
    return _serve_webp_from_data_url(refs[idx])


@image_gen_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    want_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def early(msg):
        if want_json:
            return jsonify(ok=False, error=msg), 400
        flash(msg, "warning")
        return redirect(url_for("image_gen.workbench"))

    cfg = get_site_config()
    if not cfg.image_base_url or not cfg.image_api_key:
        return early("生图服务尚未配置，请联系管理员")

    model_id = request.form.get("model", type=int)
    model = db.session.get(GenerationModel, model_id) if model_id else None
    if not model or not model.enabled:
        return early("请选择有效的生图模型")

    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return early("请输入提示词")

    aspect = request.form.get("size", "auto")
    if aspect not in ASPECT_TO_SIZE:
        aspect = "auto"
    size = ASPECT_TO_SIZE[aspect]

    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(count, MAX_COUNT))

    # 参考图（最多 5 张）
    references = []
    ref_b64_list = []
    for f in request.files.getlist("references")[:MAX_REFERENCES]:
        if f and f.filename:
            data = f.read()
            if data:
                references.append((f.filename, data, f.mimetype or "image/png"))
                try:
                    ref_b64_list.append(raw_bytes_to_webp_data_url(data))
                except Exception:
                    pass
    ref_count = len(references)
    if ref_count:
        labels = "、".join(f"图片{i + 1}" for i in range(ref_count))
        prompt = (
            f"参考图片编号：{labels}。"
            f"请按这些编号理解提示词中的图片引用。\n\n{prompt}"
        )

    # 预估算积分，点数不足禁止生成
    estimated = count * (model.points_per_image or 0)
    balance = current_user.points or 0
    if balance < estimated:
        msg = f"点数不足：本次预计消耗 {estimated} 点，当前余额 {balance} 点"
        if want_json:
            return jsonify(ok=False, code="insufficient_points", error=msg), 400
        flash(msg, "warning")
        return redirect(url_for("image_gen.workbench"))

    # 加锁定，进入生成任务
    ACTIVE_GENERATION_TASKS.add(current_user.id)
    try:
        # 调用生图
        try:
            images = generate_images(
                base_url=cfg.image_base_url,
                api_key=cfg.image_api_key,
                model=model.name,
                prompt=prompt,
                size=size,
                n=count,
                references=references,
            )
        except Exception as e:
            log = GenerationLog(
                user_id=current_user.id,
                model_id=model.id,
                model_name=model.display_name,
                prompt=prompt,
                size=size,
                count=count,
                references_count=ref_count,
                status="failed",
                images="[]",
                reference_images=json.dumps(ref_b64_list),
                points_spent=0,
                error=str(e)[:500],
            )
            db.session.add(log)
            db.session.commit()
            if want_json:
                return jsonify(ok=False, error=str(e)[:200], log_id=log.id), 200
            flash(f"生图失败：{str(e)[:200]}", "danger")
            return redirect(url_for("image_gen.workbench"))

        actual = len(images)
        spent = actual * (model.points_per_image or 0)
        status = "success" if actual == count else ("partial" if actual > 0 else "failed")

        log = GenerationLog(
            user_id=current_user.id,
            model_id=model.id,
            model_name=model.display_name,
            prompt=prompt,
            size=size,
            count=count,
            references_count=ref_count,
            status=status,
            images=json.dumps(images, ensure_ascii=False),
            reference_images=json.dumps(ref_b64_list),
            points_spent=spent,
        )
        db.session.add(log)
        if spent:
            current_user.points = balance - spent
            db.session.add(
                PointTransaction(
                    user_id=current_user.id,
                    delta=-spent,
                    balance_after=current_user.points,
                    reason=f"生图消耗（{model.display_name} ×{actual}）",
                    source="consume",
                )
            )
        db.session.commit()

        if want_json:
            return jsonify(
                ok=True,
                log_id=log.id,
                model_name=model.display_name,
                size=size,
                images=[
                    url_for("image_gen.output_image", log_id=log.id, idx=i)
                    for i in range(len(images))
                ],
                points_spent=spent,
                balance=current_user.points,
                status=status,
            )
        flash(f"生图完成，成功 {actual} 张，消耗 {spent} 点", "success")
        return redirect(url_for("image_gen.log_detail", log_id=log.id))
    finally:
        ACTIVE_GENERATION_TASKS.discard(current_user.id)


@image_gen_bp.route("/api/logs")
@login_required
def api_logs():
    page = request.args.get("page", 1, type=int)
    pagination = (
        GenerationLog.query.filter_by(user_id=current_user.id)
        .order_by(GenerationLog.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    items = []
    for l in pagination.items:
        imgs = l.image_list()
        if imgs:
            items.append({
                "id": l.id,
                "first_image": url_for(
                    "image_gen.output_image", log_id=l.id, idx=0
                ),
                "model_name": l.model_name,
                "size": l.size or "auto",
                "count": l.count,
                "points_spent": l.points_spent,
                "status": l.status,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M"),
                "detail_url": url_for("image_gen.log_detail", log_id=l.id),
            })
    return jsonify({
        "ok": True,
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
        "items": items,
    })


@image_gen_bp.route("/logs")
@login_required
def logs():
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" and request.args.get("json"):
        return api_logs()
    page = request.args.get("page", 1, type=int)
    pagination = (
        GenerationLog.query.filter_by(user_id=current_user.id)
        .order_by(GenerationLog.created_at.desc())
        .paginate(page=page, per_page=12, error_out=False)
    )
    return render_template(
        "image_gen/logs.html", pagination=pagination, logs=pagination.items
    )


@image_gen_bp.route("/logs/<int:log_id>")
@login_required
def log_detail(log_id):
    log = db.session.get(GenerationLog, log_id)
    if not log:
        abort(404)
    if log.user_id != current_user.id and not current_user.is_super_admin:
        abort(403)
    return render_template("image_gen/log_detail.html", log=log)
