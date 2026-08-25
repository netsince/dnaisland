"""用户侧生图工作台：生图、我的记录与详情。

权限：仅登录用户；生成前校验点数，不足直接禁止。
扣费：完成后按实产张数 × 模型每图积分扣减（写入 PointTransaction）。
"""

import base64
import json
import threading
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import GenerationLog, GenerationModel, GenerationTask
from ..services.generation_worker import process_generation_task, recover_stale_tasks
from ..services.image_gen_service import effective_credentials
from ..services.image_service import send_webp
from ..services.site_service import get_site_config
from ..utils import ensure_owner_or_admin, is_xhr

image_gen_bp = Blueprint("image_gen", __name__, url_prefix="/image-gen")

# 宽高比 -> OpenAI size（照搬 infinite-canvas 默认 1k 分辨率）；auto 不传 size
ASPECT_TO_SIZE = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "4:3": "1408x1024",
    "3:4": "1024x1408",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
}
VALID_ASPECTS = tuple(["auto"] + list(ASPECT_TO_SIZE.keys()))
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
    # 默认选中每图积分最低的可用模型
    default_model = min(models, key=lambda m: m.points_per_image or 0) if models else None

    # 同款生成：预填提示词与参考图（来自某条历史记录的 id）
    prefill_prompt = (request.args.get("prompt") or "").strip()
    prefill_refs = []
    from_log_id = request.args.get("from_log", type=int)
    if from_log_id:
        lg = db.session.get(GenerationLog, from_log_id)
        if lg and lg.user_id == current_user.id:
            prefill_refs = lg.reference_image_list()

    return render_template(
        "image_gen/workbench.html",
        models=models,
        default_model=default_model,
        aspects=VALID_ASPECTS,
        max_refs=MAX_REFERENCES,
        prefill_prompt=prefill_prompt,
        prefill_refs=prefill_refs,
    )


def _serve_webp_from_data_url(data_url):
    # 复用 image_service.send_webp，避免重复实现 data-url 解析与发送
    return send_webp(data_url)


@image_gen_bp.route("/output/<int:log_id>/<int:idx>")
@login_required
def output_image(log_id, idx):
    """产出图（原图）接口：按 log_id + 序号返回 WEBP 二进制。"""
    log = db.session.get(GenerationLog, log_id)
    if not log:
        abort(404)
    ensure_owner_or_admin(log.user_id)
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
    ensure_owner_or_admin(log.user_id)
    refs = log.reference_image_list()
    if idx < 0 or idx >= len(refs):
        abort(404)
    ref = refs[idx]
    # 参考图在库中存为 dict（filename/mimetype/data_b64），而非 data URL 字符串；
    # 统一规范成 data URL 再转 WEBP 发送（兼容历史上可能存在的纯字符串格式）。
    if isinstance(ref, dict):
        ref = "data:{};base64,{}".format(
            ref.get("mimetype") or "image/png", ref.get("data_b64") or ""
        )
    return _serve_webp_from_data_url(ref)


@image_gen_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    want_json = is_xhr()

    def early(msg):
        if want_json:
            return jsonify(ok=False, error=msg), 400
        flash(msg, "warning")
        return redirect(url_for("image_gen.workbench"))

    cfg = get_site_config()

    model_id = request.form.get("model", type=int)
    model = db.session.get(GenerationModel, model_id) if model_id else None
    if not model or not model.enabled:
        return early("请选择有效的生图模型")

    # 凭证以模型级配置优先，缺失回退全局；两者皆无则拒绝
    base_url, api_key = effective_credentials(model, cfg)
    if not base_url or not api_key:
        return early("生图服务尚未配置，请联系管理员")

    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return early("请输入提示词")

    aspect = request.form.get("size", "auto")
    if aspect not in ASPECT_TO_SIZE:
        aspect = "auto"
    # auto 表示不指定尺寸，不传给 API；其余按比例映射为具体的 size
    size = ASPECT_TO_SIZE.get(aspect) if aspect in ASPECT_TO_SIZE else None

    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(count, MAX_COUNT))

    # 参考图：原样存储（filename/mimetype/base64），后台线程还原为字节
    ref_payload = []
    for f in request.files.getlist("references")[:MAX_REFERENCES]:
        if f and f.filename:
            data = f.read()
            if data:
                ref_payload.append(
                    {
                        "filename": f.filename,
                        "mimetype": f.mimetype or "image/png",
                        "data_b64": base64.b64encode(data).decode("ascii"),
                    }
                )
    ref_count = len(ref_payload)
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

    # 同一用户仅允许一个进行中的任务，避免并发超额扣点
    existing = GenerationTask.query.filter(
        GenerationTask.user_id == current_user.id,
        GenerationTask.status.in_(["pending", "processing"]),
    ).first()
    if existing:
        return early("已有进行中的生图任务，请稍候")

    # 创建任务即返回，实际生图在后台线程完成（避免反向代理超时）
    task = GenerationTask(
        user_id=current_user.id,
        model_id=model.id,
        model_name=model.display_name,
        prompt=prompt,
        size=size,
        count=count,
        references_count=ref_count,
        reference_data=(
            json.dumps(ref_payload, ensure_ascii=False) if ref_payload else None
        ),
        status="pending",
    )
    db.session.add(task)
    db.session.commit()

    threading.Thread(
        target=process_generation_task,
        args=(current_app._get_current_object(), task.id),  # type: ignore[attr-defined]
        daemon=True,
    ).start()

    if want_json:
        return jsonify(ok=True, task_id=task.id)
    flash("已提交生图任务，生成中…", "info")
    return redirect(url_for("image_gen.workbench"))


@image_gen_bp.route("/api/tasks", methods=["GET"])
@login_required
def api_tasks():
    """返回当前用户进行中的任务（pending/processing），不含任何历史任务。"""
    recover_stale_tasks(current_app._get_current_object())  # type: ignore[attr-defined]
    tasks = (
        GenerationTask.query.filter(GenerationTask.user_id == current_user.id)
        .filter(GenerationTask.status.in_(["pending", "processing"]))
        .order_by(GenerationTask.created_at.desc())
        .all()
    )
    data = [
        {
            "id": t.id,
            "status": t.status,
            "model_name": t.model_name,
            "size": t.size or "auto",
            "count": t.count,
            "created_at": (
                (t.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                if t.created_at
                else ""
            ),
        }
        for t in tasks
    ]
    return jsonify(ok=True, tasks=data)


@image_gen_bp.route("/api/tasks/<int:task_id>", methods=["GET"])
@login_required
def api_task_detail(task_id):
    """任务详情：用于轮询发现任务完成后，拉取最终状态/错误/结果日志。"""
    t = db.session.get(GenerationTask, task_id)
    if not t or t.user_id != current_user.id:
        return jsonify(ok=False, error="任务不存在"), 404
    points_spent = 0
    if t.result_log_id:
        log = db.session.get(GenerationLog, t.result_log_id)
        if log:
            points_spent = log.points_spent or 0
    return jsonify(
        ok=True,
        id=t.id,
        status=t.status,
        error=t.error,
        log_id=t.result_log_id,
        points_spent=points_spent,
        balance=current_user.points,
    )


from sqlalchemy.orm import defer


@image_gen_bp.route("/api/logs")
@login_required
def api_logs():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 12, type=int)
    pagination = (
        GenerationLog.query.options(
            defer(GenerationLog.images),
            defer(GenerationLog.reference_images),
        )
        .filter_by(user_id=current_user.id)
        .order_by(GenerationLog.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    items = []
    for item in pagination.items:
        img_count = (item.count or 1) if item.status != "failed" else 0
        ref_count = item.references_count or 0
        if img_count > 0:
            items.append({
                "id": item.id,
                "first_image": url_for(
                    "image_gen.output_image", log_id=item.id, idx=0
                ),
                "images": [
                    url_for("image_gen.output_image", log_id=item.id, idx=i)
                    for i in range(img_count)
                ],
                "references": [
                    url_for("image_gen.reference_image", log_id=item.id, idx=i)
                    for i in range(ref_count)
                ],
                "prompt": item.prompt,
                "model_name": item.model_name,
                "size": item.size or "auto",
                "count": item.count,
                "points_spent": item.points_spent,
                "status": item.status,
                "created_at": (item.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if item.created_at else "",
                "detail_url": url_for("image_gen.log_detail", log_id=item.id),
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
    if is_xhr() and request.args.get("json"):
        return api_logs()
    return render_template("image_gen/logs.html")


@image_gen_bp.route("/logs/<int:log_id>")
@login_required
def log_detail(log_id):
    log = db.session.get(GenerationLog, log_id)
    if not log:
        abort(404)
    ensure_owner_or_admin(log.user_id)
    return render_template("image_gen/log_detail.html", log=log)
