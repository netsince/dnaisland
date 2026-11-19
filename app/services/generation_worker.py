"""生图异步任务处理：在后台线程中执行实际生图并落库。

设计要点：
- ``/generate`` 仅创建 GenerationTask 并立即返回 task_id（请求快速返回，避免反向代理超时）。
- 后台线程调用 ``process_generation_task`` 完成实际生图、写 GenerationLog、扣点。
- 任务状态持久化在数据库，因此刷新/重开页面、多 worker 都能继续轮询跟踪。
"""

import base64
import json
from datetime import datetime, timedelta

from flask import has_app_context
from sqlalchemy import and_, func, or_

from ..constants import points_mul, points_sub
from ..extensions import db
from ..models import GenerationLog, GenerationModel, GenerationTask, PointTransaction, User
from ..services.image_gen_service import effective_credentials, generate_images
from ..services.site_service import get_site_config


def _build_reference_files(task):
    """将任务中保存的参考图 JSON 还原为 (filename, bytes, mimetype) 列表。"""
    out = []
    for r in task.reference_list():
        try:
            out.append(
                (
                    r.get("filename", "ref"),
                    base64.b64decode(r["data_b64"]),
                    r.get("mimetype", "image/png"),
                )
            )
        except Exception:
            continue
    return out


def process_generation_task(app, task_id):
    """后台线程入口：认领任务 -> 生图 -> 写日志/扣点 -> 更新任务状态。"""
    with app.app_context():
        task = db.session.get(GenerationTask, task_id)
        if task is None or task.status != "pending":
            return
        task.status = "processing"
        db.session.commit()

        cfg = get_site_config()
        model = db.session.get(GenerationModel, task.model_id)
        if model is None:
            _fail_task(task, "生图模型不存在，任务已终止", model)
            return
        user = db.session.get(User, task.user_id)
        ref_files = _build_reference_files(task)

        base_url, api_key = effective_credentials(model, cfg)
        if not base_url or not api_key:
            _fail_task(task, "生图服务未配置 API（该模型与全局均未配置）", model)
            return

        try:
            images = generate_images(
                base_url=base_url,
                api_key=api_key,
                model=model.name,
                prompt=task.prompt,
                size=task.size,
                n=task.count,
                references=ref_files,
            )
        except Exception as e:
            _fail_task(task, str(e)[:500], model)
            return

        _finish_task_success(task, model, user, images)


def _finish_task_success(task, model, user, images):
    """任务成功的唯一收尾入口：写日志、扣点、清空中转数据。

    抽成函数是为了让「清空 reference_data」这件事可被单独测试 —— 之前它
    内联在 process_generation_task 里，而那个函数会打外部生图 API，
    测试只能手抄一段等价代码，等于没测到真实路径。
    """
    actual = len(images)
    spent = points_mul(actual, model.points_per_image)
    status = "success" if actual == task.count else ("partial" if actual > 0 else "failed")
    log = GenerationLog(
        user_id=task.user_id,
        model_id=model.id,
        model_name=model.display_name,
        prompt=task.prompt,
        size=task.size,
        count=task.count,
        references_count=task.references_count,
        status=status,
        images=json.dumps(images, ensure_ascii=False) if images else None,
        reference_images=task.reference_data or None,
        points_spent=spent,
    )
    db.session.add(log)
    db.session.flush()
    if spent and user:
        user.points = points_sub(user.points, spent)
        db.session.add(
            PointTransaction(
                user_id=user.id,
                delta=-spent,
                balance_after=user.points,
                reason=f"生图消耗（{model.display_name} ×{actual}）",
                source="consume",
            )
        )
    task.result_log_id = log.id
    task.status = "succeeded"
    # 参考图是任务的**中转数据**：只需活到后台线程把它喂给模型为止。
    # 落进 GenerationLog 之后任务表这份就是纯冗余（库里实测 843MB 是它），
    # 立刻清空。用户回看走的是 log.reference_images，不受影响。
    task.reference_data = None
    db.session.commit()
    return log


def _fail_task(task, error, model):
    """生图失败时写一条 failed 日志，并把任务标记为 failed。"""
    try:
        log = GenerationLog(
            user_id=task.user_id,
            model_id=model.id if model else None,
            model_name=model.display_name if model else None,
            prompt=task.prompt,
            size=task.size,
            count=task.count,
            references_count=task.references_count,
            status="failed",
            images=None,
            reference_images=task.reference_data or None,
            points_spent=0,
            error=error,
        )
        db.session.add(log)
        db.session.flush()
        task.result_log_id = log.id
        task.status = "failed"
        task.error = error
        # 失败同样是终态：参考图只在 _build_reference_files 里用一次，
        # 那条路径在走到这里之前就结束了。中转数据一并清掉。
        task.reference_data = None
        db.session.commit()
    except Exception:
        db.session.rollback()


# ---------------------------------------------------------------------------
#  进行中任务的收敛
# ---------------------------------------------------------------------------
#
# 任务的「进行中」只活在内存里：真正的生图跑在 daemon 线程上，进程一没，线程就没了，
# 而库里那一行还写着 pending/processing。不收敛它就等于给这个用户上了一把永久的锁 ——
# /generate 的「同一用户只允许一个进行中的任务」会一直拒绝他，直到有人手工删行
# （2026-11-19 事故：一条 processing 卡了三个半小时，用户每次生图都被挡住）。
#
# 两个状态都要扫，阈值分开：pending 正常只存在几毫秒（提交事务后立刻起线程认领），
# 5 分钟已经很宽；processing 要等模型返回，放宽到 15 分钟，避免误杀真在跑的任务。

IN_FLIGHT_STATUSES = ("pending", "processing")
PENDING_STALE_MINUTES = 5
PROCESSING_STALE_MINUTES = 15
_STALE_ERRORS = {
    "pending": "任务未被领走（服务重启或进程中断），请重试",
    "processing": "任务处理超时（进程可能已重启，请重试）",
}


def recover_stale_tasks(app=None, *, user_id=None, now=None):
    """把已经死掉的进行中任务收敛成 failed，返回收敛的条数。

    [app] 不传时要求当前已在应用上下文里；传了且当前没有上下文就自己 push 一个
    （后台线程场景）。[user_id] 给定时只扫这个用户 —— /generate 在判断「有没有进行中
    的任务」之前必须先收敛他自己那条，否则从不轮询任务列表的客户端永远解不开。
    """
    if app is not None and not has_app_context():
        with app.app_context():
            return _recover_stale_tasks(user_id=user_id, now=now)
    if not has_app_context():
        raise RuntimeError("recover_stale_tasks() 需要应用上下文，或显式传入 app")
    return _recover_stale_tasks(user_id=user_id, now=now)


def _recover_stale_tasks(*, user_id=None, now=None):
    now = now or datetime.utcnow()
    # updated_at 是「最后一次有动静」的时间；为 NULL 时退回 created_at ——
    # 否则 NULL 行永远不满足比较条件，等于给这些行开了免死金牌。
    last_seen = func.coalesce(GenerationTask.updated_at, GenerationTask.created_at)
    query = GenerationTask.query.filter(
        or_(
            and_(
                GenerationTask.status == "pending",
                last_seen < now - timedelta(minutes=PENDING_STALE_MINUTES),
            ),
            and_(
                GenerationTask.status == "processing",
                last_seen < now - timedelta(minutes=PROCESSING_STALE_MINUTES),
            ),
        )
    )
    if user_id is not None:
        query = query.filter(GenerationTask.user_id == user_id)
    stale = query.all()
    if not stale:
        return 0
    try:
        for task in stale:
            task.error = _STALE_ERRORS.get(task.status, _STALE_ERRORS["processing"])
            task.status = "failed"
            # 终态：中转的参考图不再有任何用途（与 _fail_task 保持一致）。
            task.reference_data = None
        db.session.commit()
    except Exception:
        db.session.rollback()
        return 0
    return len(stale)


def live_tasks(user_id, app=None):
    """该用户真正在跑的任务（**先收敛死任务，再查**）。

    这两步必须成对使用：只查不收敛，一条死任务就能把用户永久挡在门外。
    """
    recover_stale_tasks(app, user_id=user_id)
    return (
        GenerationTask.query.filter(
            GenerationTask.user_id == user_id,
            GenerationTask.status.in_(IN_FLIGHT_STATUSES),
        )
        .order_by(GenerationTask.created_at.desc())
        .all()
    )
