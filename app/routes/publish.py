import json

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..services.card_import_service import parse_export_package
from ..services.card_publish_service import create_card_from_payload

publish_bp = Blueprint("publish", __name__, url_prefix="/publish")


@publish_bp.route("/")
@login_required
def start():
    return render_template("publish/start.html")


@publish_bp.route("/parse", methods=["POST"])
@login_required
def parse():
    raw = (request.form.get("json") or "").strip()
    if not raw:
        flash("请粘贴 JSON 内容", "danger")
        return render_template("publish/start.html")
    try:
        data = parse_export_package(raw)
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_template("publish/start.html")
    return render_template(
        "publish/edit.html",
        prefill=data,
        dialogue_initial=data["dialogue_style"],
        images_initial=data["images"],
    )


@publish_bp.route("/edit", methods=["GET", "POST"])
@login_required
def edit():
    if request.method == "GET":
        return render_template(
            "publish/edit.html",
            prefill=None,
            dialogue_initial=[],
            images_initial={},
        )

    tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]

    dialogue_style = []
    try:
        ds_list = json.loads(request.form.get("dialogue_style_json") or "[]")
        if isinstance(ds_list, list):
            for item in ds_list:
                if isinstance(item, dict):
                    dialogue_style.append(
                        {
                            "user": str(item.get("user") or ""),
                            "assistant": str(item.get("assistant") or ""),
                        }
                    )
    except json.JSONDecodeError:
        dialogue_style = []

    images = {}
    try:
        keep = json.loads(request.form.get("images_keep_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        keep = {}
    if not isinstance(keep, dict):
        keep = {}
    for slot in ("square", "landscape", "portrait"):
        f = request.files.get("image_" + slot)
        if f and f.filename:
            raw = f.read()
            if raw:
                images[slot] = raw  # 字节交由共享服务压缩
                continue
        if keep.get(slot):
            images[slot] = str(keep[slot])

    payload = {
        "name": request.form.get("name"),
        "gender": request.form.get("gender"),
        "persona": request.form.get("persona"),
        "intro": request.form.get("intro"),
        "opening": request.form.get("opening"),
        "original_link": request.form.get("original_link"),
        "cover_focus": request.form.get("cover_focus"),
        "seed": request.form.get("seed"),
        "tags": tags,
        "dialogue_style": dialogue_style,
        "images": images,
    }
    card, error = create_card_from_payload(current_user, payload)
    if error:
        current_app.logger.exception("发布角色卡失败: %s", error)
        flash("提交失败，请稍后重试", "danger")
        return render_template(
            "publish/edit.html",
            prefill=request.form,
            dialogue_initial=dialogue_style,
            images_initial=images,
        )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("发布角色卡写入数据库失败")
        flash("提交失败，请稍后重试", "danger")
        return render_template(
            "publish/edit.html",
            prefill=request.form,
            dialogue_initial=dialogue_style,
            images_initial=images,
        )

    flash("角色卡已提交，等待审核", "success")
    return redirect(url_for("publish.done"))


@publish_bp.route("/done")
@login_required
def done():
    return render_template("publish/done.html")
