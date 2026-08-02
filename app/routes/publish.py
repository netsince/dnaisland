import json
import uuid

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
from ..models import Card, CardDialogueStyle, CardImage, CardTag
from ..services.card_import_service import parse_export_package
from ..services.image_service import (
    compress_image,
    optimize_image_for_export,
    raw_bytes_to_webp_data_url,
)

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

    name = (request.form.get("name") or "").strip()
    gender = (request.form.get("gender") or "").strip()
    persona = request.form.get("persona") or ""
    intro = request.form.get("intro") or ""
    opening = request.form.get("opening") or ""
    original_link = request.form.get("original_link") or ""
    cover_focus = request.form.get("cover_focus") or None
    # 语音合成 seed：非必填，空白/非法时置为 None（交由客户端决定音色）
    seed_raw = (request.form.get("seed") or "").strip()
    seed = None
    if seed_raw:
        try:
            seed = int(seed_raw)
        except ValueError:
            seed = None
    # 不读取客户端传入的 id，始终由平台自动分配新 id
    card_id = str(uuid.uuid4())

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
                images[slot] = raw_bytes_to_webp_data_url(raw, max_edge=1024, quality=80)
                continue
        if keep.get(slot):
            images[slot] = compress_image(str(keep[slot]))

    card = Card(
        id=card_id,
        author_id=current_user.id,
        name=name,
        gender=gender or "无性",
        persona=persona,
        intro=intro,
        opening=opening,
        original_link=original_link or None,
        cover_focus=cover_focus,
        seed=seed,
        status="pending",  # 未审核
    )
    db.session.add(card)
    for tag in tags:
        db.session.add(CardTag(card_id=card_id, tag=tag))
    for idx, turn in enumerate(dialogue_style):
        db.session.add(
            CardDialogueStyle(
                card_id=card_id,
                turn_index=idx,
                user_text=turn["user"],
                assistant_text=turn["assistant"],
            )
        )
    for slot, data_uri in images.items():
        # 发布上传时即做“复制导出专用”轻度压缩，并打上 optimized 标记，
        # 使后续复制导出直接复用、无需再压缩。
        optimized_data = optimize_image_for_export(data_uri)
        db.session.add(
            CardImage(
                card_id=card_id, slot=slot, data=optimized_data, optimized=True
            )
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
