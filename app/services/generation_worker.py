"""生图异步任务处理：在后台线程中执行实际生图并落库。

设计要点：
- ``/generate`` 仅创建 GenerationTask 并立即返回 task_id（请求快速返回，避免反向代理超时）。
- 后台线程调用 ``process_generation_task`` 完成实际生图、写 GenerationLog、扣点。
- 任务状态持久化在数据库，因此刷新/重开页面、多 worker 都能继续轮询跟踪。
"""

import base64
import json
from datetime import datetime, timedelta

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

        actual = len(images)
        spent = actual * (model.points_per_image or 0)
        status = (
            "success"
            if actual == task.count
            else ("partial" if actual > 0 else "failed")
        )
        log = GenerationLog(
            user_id=task.user_id,
            model_id=model.id,
            model_name=model.display_name,
            prompt=task.prompt,
            size=task.size,
            count=task.count,
            references_count=task.references_count,
            status=status,
            images=json.dumps(images, ensure_ascii=False),
            reference_images=task.reference_data or "[]",
            points_spent=spent,
        )
        db.session.add(log)
        db.session.flush()
        if spent and user:
            user.points = (user.points or 0) - spent
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
        db.session.commit()


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
            images="[]",
            reference_images=task.reference_data or "[]",
            points_spent=0,
            error=error,
        )
        db.session.add(log)
        db.session.flush()
        task.result_log_id = log.id
        task.status = "failed"
        task.error = error
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
            if stale:
                db.session.commit()
    except Exception:
        db.session.rollback()
