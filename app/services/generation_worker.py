"""生图异步任务处理：在后台线程中执行实际生图并落库。

设计要点：
- ``/generate`` 仅创建 GenerationTask 并立即返回 task_id（请求快速返回，避免反向代理超时）。
- 后台线程调用 ``process_generation_task`` 完成实际生图、写 GenerationLog、扣点。
- 任务状态持久化在数据库，因此刷新/重开页面、多 worker 都能继续轮询跟踪。
"""

import base64
import json
from datetime import datetime, timedelta

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


def recover_stale_tasks(app):
    """将卡在 processing 超过阈值的任务标记为失败（进程可能中途重启）。"""
    try:
        with app.app_context():
            threshold = datetime.utcnow() - timedelta(minutes=15)
            stale = GenerationTask.query.filter(
                GenerationTask.status == "processing",
                GenerationTask.updated_at < threshold,
            ).all()
            for t in stale:
                t.status = "failed"
                t.error = "任务处理超时（进程可能已重启，请重试）"
                # 超时任务同样是终态，中转数据不再有任何用途。
                t.reference_data = None
            if stale:
                db.session.commit()
    except Exception:
        db.session.rollback()
