from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import Card, CardTag, Notification

user_bp = Blueprint("user", __name__)


@user_bp.route("/user/<username>")
def profile(username):
    u = User_query_by_username(username)
    if not u:
        abort(404)
    cards = (
        Card.query.filter_by(author_id=u.id)
        .order_by(Card.created_at.desc())
        .all()
    )
    return render_template(
        "user/profile.html",
        u=u,
        cards=cards,
        is_self=(current_user.is_authenticated and current_user.id == u.id),
    )


@user_bp.route("/my/cards")
@login_required
def my_cards():
    cards = (
        Card.query.filter_by(author_id=current_user.id)
        .order_by(Card.created_at.desc())
        .all()
    )
    stats = dict(
        db.session.query(Card.status, func.count(Card.id))
        .filter(Card.author_id == current_user.id)
        .group_by(Card.status)
        .all()
    )
    return render_template(
        "user/my_cards.html",
        cards=cards,
        stats=stats,
        pending=stats.get("pending", 0),
        approved=stats.get("approved", 0),
        rejected=stats.get("rejected", 0),
    )


@user_bp.route("/notifications")
@login_required
def notifications():
    items = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    unread = sum(1 for n in items if not n.is_read)
    return render_template("user/notifications.html", items=items, unread=unread)


@user_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    from ..services.notification_service import mark_all_read

    mark_all_read(current_user.id)
    flash("已全部标记为已读", "success")
    return redirect(url_for("user.notifications"))


def User_query_by_username(username):
    from ..models import User

    return User.query.filter_by(username=username).first()
