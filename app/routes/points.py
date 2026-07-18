"""用户侧点数中心：详情（余额 + 变化明细）与兑换（逐码兑换，带限流）。

限流策略（进程内，单进程部署足够）：
- 单次请求最多兑换 50 个 key；
- 同一用户每分钟最多发起 2 次兑换请求；
- 连续多次（默认 3 次）兑换「全部失败」则临时禁用兑换 1 小时。
"""

import time

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import KeyUsageLog, PointTransaction, RedemptionKey

points_bp = Blueprint("points", __name__, url_prefix="/points")

# ---------------------------------------------------------------------------
# 限流状态（进程内）
# ---------------------------------------------------------------------------
_REDEEM_STATE = {}  # user_id -> {"reqs":[ts...], "fail_streak":int, "locked_until":float}

MAX_KEYS_PER_REQUEST = 50
MAX_REQUESTS_PER_MINUTE = 2
FAIL_STREAK_LIMIT = 3
LOCK_SECONDS = 3600


def _get_state(uid):
    return _REDEEM_STATE.setdefault(
        uid, {"reqs": [], "fail_streak": 0, "locked_until": 0.0}
    )


def redeem_allowed(uid):
    """返回 (ok, message)。仅做检查，不修改状态。"""
    now = time.time()
    st = _get_state(uid)
    if now < st["locked_until"]:
        remain = int(st["locked_until"] - now)
        return False, f"兑换功能已被临时限制，请于 {remain // 60} 分 {remain % 60} 秒后重试"
    st["reqs"] = [t for t in st["reqs"] if now - t < 60]
    if len(st["reqs"]) >= MAX_REQUESTS_PER_MINUTE:
        return False, "操作过于频繁，每分钟最多兑换 2 次"
    return True, ""


def record_redeem(uid, had_success):
    """记录一次兑换请求的请求时间戳与连续失败计数。"""
    now = time.time()
    st = _get_state(uid)
    st["reqs"].append(now)
    if had_success:
        st["fail_streak"] = 0
    else:
        st["fail_streak"] += 1
        if st["fail_streak"] >= FAIL_STREAK_LIMIT:
            st["locked_until"] = now + LOCK_SECONDS


def redeem_status_info(uid):
    """供模板展示当前是否被限制。"""
    st = _REDEEM_STATE.get(uid)
    if not st:
        return None
    now = time.time()
    if now < st["locked_until"]:
        remain = int(st["locked_until"] - now)
        return {"locked": True, "remain_text": f"{remain // 60} 分 {remain % 60} 秒"}
    return None


# ---------------------------------------------------------------------------
# 详情页：余额大卡片 + 变化明细分页
# ---------------------------------------------------------------------------
@points_bp.route("/")
@login_required
def detail():
    page = request.args.get("page", 1, type=int)
    query = PointTransaction.query.filter_by(user_id=current_user.id).order_by(
        PointTransaction.created_at.desc()
    )
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "points/detail.html",
        balance=current_user.points or 0,
        pagination=pagination,
        txs=pagination.items,
    )


# ---------------------------------------------------------------------------
# 兑换页：多行输入框 + 限流兑换
# ---------------------------------------------------------------------------
@points_bp.route("/redeem", methods=["GET", "POST"])
@login_required
def redeem():
    if request.method == "POST":
        ok, msg = redeem_allowed(current_user.id)
        if not ok:
            flash(msg, "warning")
            return redirect(url_for("points.redeem"))

        raw = request.form.get("keys", "")
        codes = [c.strip() for c in raw.splitlines() if c.strip()]
        codes = list(dict.fromkeys(codes))  # 去重并保持顺序

        if not codes:
            flash("请输入至少一个兑换码", "warning")
            return redirect(url_for("points.redeem"))
        if len(codes) > MAX_KEYS_PER_REQUEST:
            flash(f"一次最多兑换 {MAX_KEYS_PER_REQUEST} 个兑换码", "warning")
            return redirect(url_for("points.redeem"))

        results = []  # (code, ok, message)
        success_count = 0
        for code in codes:
            key = RedemptionKey.query.filter_by(code=code).first()
            if not key:
                results.append((code, False, "兑换码不存在"))
                db.session.add(
                    KeyUsageLog(code=code, user_id=current_user.id,
                                status="fail", note="兑换码不存在")
                )
                continue
            if not key.active:
                results.append((code, False, "兑换码已被禁用"))
                db.session.add(
                    KeyUsageLog(key_id=key.id, code=code, user_id=current_user.id,
                                status="fail", note="兑换码已被禁用")
                )
                continue
            if not key.is_valid_now():
                results.append((code, False, "兑换码不在有效期内"))
                db.session.add(
                    KeyUsageLog(key_id=key.id, code=code, user_id=current_user.id,
                                status="fail", note="不在有效期内")
                )
                continue
            if key.used_count >= key.max_uses:
                results.append((code, False, "兑换码已达使用上限"))
                db.session.add(
                    KeyUsageLog(key_id=key.id, code=code, user_id=current_user.id,
                                status="fail", note="已达使用上限")
                )
                continue
            used_by_user = KeyUsageLog.query.filter_by(
                key_id=key.id, user_id=current_user.id, status="success"
            ).count()
            if used_by_user >= key.per_user_limit:
                results.append((code, False, "你已使用过该兑换码"))
                db.session.add(
                    KeyUsageLog(key_id=key.id, code=code, user_id=current_user.id,
                                status="fail", note="单人使用次数已达上限")
                )
                continue

            # 通过校验：发放点数
            current_user.points = (current_user.points or 0) + key.points
            key.used_count += 1
            db.session.add(
                PointTransaction(
                    user_id=current_user.id,
                    delta=key.points,
                    balance_after=current_user.points,
                    reason=f"兑换码 {code}",
                    source="redeem",
                    related_key=code,
                )
            )
            db.session.add(
                KeyUsageLog(
                    key_id=key.id, code=code, user_id=current_user.id,
                    points_gained=key.points, status="success",
                )
            )
            results.append((code, True, f"+{key.points} 点数"))
            success_count += 1

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("兑换失败，请稍后重试", "danger")
            return redirect(url_for("points.redeem"))

        record_redeem(current_user.id, success_count > 0)
        # 通过 session 传递结果，避免刷新页面重复兑换
        session["redeem_results"] = [
            {"code": c, "ok": o, "msg": m} for c, o, m in results
        ]
        flash(
            f"兑换完成：成功 {success_count} 个，失败 {len(codes) - success_count} 个",
            "success" if success_count else "warning",
        )
        return redirect(url_for("points.redeem"))

    # GET
    results = session.pop("redeem_results", None)
    return render_template(
        "points/redeem.html",
        results=results,
        status_info=redeem_status_info(current_user.id),
    )
