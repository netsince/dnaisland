"""用户侧点数中心：详情（余额 + 变化明细）与兑换（逐码兑换，带限流）。

限流策略（进程内，单进程部署足够）：
- 单次请求最多兑换 50 个 key；
- 同一用户每分钟最多发起 2 次兑换请求；
- 连续多次（默认 3 次）兑换「全部失败」则临时禁用兑换 1 小时。
"""

import time

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import KeyUsageLog, PointTransaction, RedemptionKey
from ..utils import rate_hit

points_bp = Blueprint("points", __name__, url_prefix="/points")

# ---------------------------------------------------------------------------
# 限流状态（进程内）
# ---------------------------------------------------------------------------
_REDEEM_STATE: dict[int, dict[str, float]] = {}  # user_id -> {"fail_streak":int, "locked_until":float}

MAX_KEYS_PER_REQUEST = 50
MAX_REQUESTS_PER_MINUTE = 2
FAIL_STREAK_LIMIT = 3
LOCK_SECONDS = 3600


def _get_state(uid):
    return _REDEEM_STATE.setdefault(
        uid, {"fail_streak": 0, "locked_until": 0.0}
    )


def redeem_allowed(uid):
    """返回 (ok, message)。仅做检查，不修改状态。

    每分钟限流复用统一的进程内限流 rate_hit；连续失败锁定时长仍由本模块维护。
    """
    now = time.time()
    st = _get_state(uid)
    if now < st["locked_until"]:
        remain = int(st["locked_until"] - now)
        return False, f"兑换功能已被临时限制，请于 {remain // 60} 分 {remain % 60} 秒后重试"
    if rate_hit("redeem", limit=MAX_REQUESTS_PER_MINUTE, per=60, key=uid):
        return False, "操作过于频繁，每分钟最多兑换 2 次"
    return True, ""


def record_redeem(uid, had_success):
    """记录一次兑换的连续失败计数（每分钟限流由 rate_hit 维护）。"""
    now = time.time()
    st = _get_state(uid)
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
# 共享逻辑：Web 路由与 App API 都调用同一组函数
# ---------------------------------------------------------------------------
def redeem_codes(viewer, codes):
    """兑换一组兑换码（codes 需为已 strip/去重 的码列表）。

    返回 (ok, message, results, success_count)：
    - ok=False 表示未兑换（限流/频率/写入失败），results 为空；
    - results 为每条码结果：{"code", "ok", "message"}。
    """
    ok_flag, message = redeem_allowed(viewer.id)
    if not ok_flag:
        return False, message, [], 0

    results: list[dict] = []
    success_count = 0
    for code in codes:
        key = RedemptionKey.query.filter_by(code=code).first()
        if not key:
            results.append({"code": code, "ok": False, "message": "兑换码不存在"})
            db.session.add(
                KeyUsageLog(code=code, user_id=viewer.id, status="fail", note="兑换码不存在")
            )
            continue
        if not key.active:
            results.append({"code": code, "ok": False, "message": "兑换码已被禁用"})
            db.session.add(
                KeyUsageLog(key_id=key.id, code=code, user_id=viewer.id,
                            status="fail", note="兑换码已被禁用")
            )
            continue
        if not key.is_valid_now():
            results.append({"code": code, "ok": False, "message": "兑换码不在有效期内"})
            db.session.add(
                KeyUsageLog(key_id=key.id, code=code, user_id=viewer.id,
                            status="fail", note="不在有效期内")
            )
            continue
        if key.used_count >= key.max_uses:
            results.append({"code": code, "ok": False, "message": "已达使用上限"})
            db.session.add(
                KeyUsageLog(key_id=key.id, code=code, user_id=viewer.id,
                            status="fail", note="已达使用上限")
            )
            continue
        used_by_user = KeyUsageLog.query.filter_by(
            key_id=key.id, user_id=viewer.id, status="success"
        ).count()
        if used_by_user >= key.per_user_limit:
            results.append({"code": code, "ok": False, "message": "你已使用过该兑换码"})
            db.session.add(
                KeyUsageLog(key_id=key.id, code=code, user_id=viewer.id,
                            status="fail", note="单人使用次数已达上限")
            )
            continue

        viewer.points = (viewer.points or 0) + key.points
        key.used_count += 1
        db.session.add(
            PointTransaction(
                user_id=viewer.id, delta=key.points, balance_after=viewer.points,
                reason=f"兑换码 {code}", source="redeem", related_key=code,
            )
        )
        db.session.add(
            KeyUsageLog(key_id=key.id, code=code, user_id=viewer.id,
                        points_gained=key.points, status="success")
        )
        results.append({"code": code, "ok": True, "message": f"+{key.points} 点数"})
        success_count += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False, "兑换失败，请稍后重试", [], 0

    record_redeem(viewer.id, success_count > 0)
    return True, "", results, success_count


def point_transactions(viewer, page=1, per_page=20):
    """返回指定用户的变化明细分页对象（按时间倒序）。"""
    return PointTransaction.query.filter_by(user_id=viewer.id).order_by(
        PointTransaction.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)


def point_balance(viewer):
    """返回指定用户的当前点数余额。"""
    return viewer.points or 0


# ---------------------------------------------------------------------------
# 详情页：余额大卡片 + 变化明细分页
# ---------------------------------------------------------------------------
@points_bp.route("/")
@login_required
def detail():
    page = request.args.get("page", 1, type=int)
    pagination = point_transactions(current_user, page=page, per_page=20)
    return render_template(
        "points/detail.html",
        balance=point_balance(current_user),
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

    ok_flag, message, results, success_count = redeem_codes(current_user, codes)
    if not ok_flag:
        flash(message, "warning")
        return redirect(url_for("points.redeem"))

    # 通过 session 传递结果，避免刷新页面重复兑换
    session["redeem_results"] = results
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
