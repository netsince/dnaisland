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
    card_id = (request.form.get("card_id") or "").strip() or str(uuid.uuid4())

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
        img_dict = json.loads(request.form.get("images_json") or "{}")
        if isinstance(img_dict, dict):
            for slot in ("square", "landscape", "portrait"):
                if img_dict.get(slot):
                    images[slot] = str(img_dict[slot])
    except json.JSONDecodeError:
        images = {}

    card = Card(
        id=card_id,
        author_id=current_user.id,
        name=name,
        gender=gender or "无性",
        persona=persona,
        intro=intro,
        opening=opening,
        original_link=original_link or None,
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
        db.session.add(CardImage(card_id=card_id, slot=slot, data=data_uri))

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
