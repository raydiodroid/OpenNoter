from app.db.models.video_tasks import VideoTask
from app.db.engine import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


# 插入任务
def insert_video_task(video_id: str, platform: str, task_id: str, user_id: int | None = None):
    db = next(get_db())
    try:
        task = VideoTask(video_id=video_id, platform=platform, task_id=task_id, user_id=user_id)
        db.add(task)
        db.commit()
        db.refresh(task)
        logger.info(
            f"Video task inserted successfully. video_id: {video_id}, platform: {platform}, "
            f"task_id: {task_id}, user_id: {user_id}"
        )
    except Exception as e:
        logger.error(f"Failed to insert video task: {e}")
    finally:
        db.close()


# 查询任务（最新一条）
def get_task_by_video(video_id: str, platform: str, user_id: int | None = None):
    db = next(get_db())
    try:
        query = db.query(VideoTask).filter_by(video_id=video_id, platform=platform)
        # 传了 user_id 就只看自己的任务，避免命中别人的缓存
        if user_id is not None:
            query = query.filter(VideoTask.user_id == user_id)
        task = query.order_by(VideoTask.created_at.desc()).first()
        if task:
            logger.info(f"Task found for video_id: {video_id} and platform: {platform}")
            return task.task_id
        else:
            logger.info(f"No task found for video_id: {video_id} and platform: {platform}")
            return None
    except Exception as e:
        logger.error(f"Failed to get task by video: {e}")
    finally:
        db.close()


# 删除任务
def delete_task_by_video(video_id: str, platform: str):
    db = next(get_db())
    try:
        tasks = (
            db.query(VideoTask)
            .filter_by(video_id=video_id, platform=platform)
            .all()
        )
        for task in tasks:
            db.delete(task)
        db.commit()
        logger.info(f"Task(s) deleted for video_id: {video_id} and platform: {platform}")
    except Exception as e:
        logger.error(f"Failed to delete task by video: {e}")
    finally:
        db.close()


# 按 task_id 删除单条任务记录，返回是否删到
def delete_task_by_task_id(task_id: str, soft: bool = True) -> bool:
    """删除任务记录。

    默认**软删除**：只写 deleted_at，保留行。因为「累计任务」「24h 失败」
    这类监控指标要能反映真实发生过的任务，不能因为用户删了笔记就倒退
    （审计结论：监控指标可被普通用户操作篡改）。

    soft=False 时才物理删除（供「彻底清理」之类的运维入口使用）。
    """
    from datetime import datetime, timezone

    db = next(get_db())
    try:
        task = db.query(VideoTask).filter_by(task_id=task_id).first()
        if not task:
            return False
        if soft:
            task.deleted_at = datetime.now(timezone.utc)
            logger.info(f"Task soft-deleted (task_id={task_id})")
        else:
            db.delete(task)
            logger.info(f"Task hard-deleted (task_id={task_id})")
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to delete task by task_id: {e}")
        return False
    finally:
        db.close()


# 取某个视频下的全部 task_id（供旧的 delete_task 按 video_id 删除时用）
def get_task_ids_by_video(video_id: str, platform: str) -> list[str]:
    db = next(get_db())
    try:
        tasks = (
            db.query(VideoTask)
            .filter_by(video_id=video_id, platform=platform)
            .order_by(VideoTask.created_at.desc())
            .all()
        )
        return [t.task_id for t in tasks]
    except Exception as e:
        logger.error(f"Failed to list task ids by video: {e}")
        return []
    finally:
        db.close()


# 按 task_id 查任务记录，不存在返回 None
def get_task_by_id(task_id: str):
    db = next(get_db())
    try:
        return db.query(VideoTask).filter_by(task_id=task_id).first()
    except Exception as e:
        logger.error(f"Failed to get task by task_id: {e}")
        return None
    finally:
        db.close()

# ---------------------------------------------------------------------------
# 任务状态：写入 DB，作为不随「删除笔记」消失的历史来源
# ---------------------------------------------------------------------------
def update_task_status(
    task_id: str,
    status: str,
    message: str | None = None,
    failed_at=None,
    *,
    user_id: int | None = None,
    platform: str | None = None,
    video_id: str | None = None,
) -> bool:
    """把任务状态同步进 DB（行不存在则创建）。

    ## 为什么要 upsert

    任务列表接口原来靠扫 `note_results/*.status.json`，成本是 O(全部文件)——
    实测 5000 个文件单次 137ms，多用户下每个用户每次刷新都要付这个代价。
    改成查 DB（有 `user_id`/`updated_at` 索引）就是 O(log n)。

    但前提是**任务一创建就要有 DB 行**，否则列表查不到刚提交的任务。
    所以：
      - 传了 platform/video_id（新建任务）→ 不存在则插入
      - 只传 task_id（状态推进）→ 不存在则跳过（保持原语义，避免
        `_update_status` 被别处调用时凭空造行）

    ## 其他语义
    - FAILED 时写 failed_at；非 FAILED 时清空 failed_at 与 error，
      避免重试成功后仍带着上一次的失败痕迹
    - updated_at 每次刷新
    - 任何异常都只记日志，绝不向上抛 —— 状态落库失败不该影响任务主流程
    """
    from datetime import datetime, timezone

    db = next(get_db())
    try:
        task = db.query(VideoTask).filter_by(task_id=task_id).first()
        if task is None:
            can_create = platform and video_id
            if not can_create:
                return False
            task = VideoTask(
                task_id=task_id,
                video_id=str(video_id),
                platform=str(platform),
                user_id=user_id,
            )
            db.add(task)
            db.flush()

        now = datetime.now(timezone.utc)
        task.status = str(status)[:32]
        task.updated_at = now
        if str(status).upper() == "FAILED":
            task.error = (message or "")[:512] or None
            task.failed_at = failed_at or now
        else:
            task.error = None
            task.failed_at = None
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update task status (task_id={task_id}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def count_tasks_by_status_since(since=None) -> dict:
    """按状态统计任务数（监控用）。

    since 给定时只统计 updated_at >= since 的行（用于 24h 失败）。
    旧库若还没跑迁移（没有 status 列），会抛 OperationalError，
    调用方应视作「DB 尚无状态数据」并回退到扫文件。
    """
    from sqlalchemy import func as sa_func

    db = next(get_db())
    try:
        # 软删除的行仍然计入「累计」（它们真实发生过），
        # 但不计入 running/success/failed 的当前态分布——
        # 那些是「此刻还在盘上的任务」的口径。
        rows = (
            db.query(VideoTask.status, sa_func.count(VideoTask.id))
            .filter(VideoTask.deleted_at.is_(None))
            .group_by(VideoTask.status)
            .all()
        )
        counters = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "running": 0,
            "unknown": 0,
        }
        for status, count in rows:
            key = (status or "").upper()
            counters["total"] += count
            if key == "SUCCESS":
                counters["success"] += count
            elif key == "FAILED":
                counters["failed"] += count
            elif key:
                counters["running"] += count
            else:
                counters["unknown"] += count

        # 累计总数：包含软删除的历史（这就是「不随删笔记倒退」的那一份）
        counters["all_including_deleted"] = (
            db.query(sa_func.count(VideoTask.id)).scalar() or 0
        )
        counters["deleted"] = (
            db.query(sa_func.count(VideoTask.id))
            .filter(VideoTask.deleted_at.isnot(None))
            .scalar()
            or 0
        )

        if since is not None:
            # 24h 失败：包含已软删除的任务——「这 24 小时里失败过几次」
            # 是发生过的事实，不该被后来删笔记抹掉。
            counters["failed_since"] = (
                db.query(sa_func.count(VideoTask.id))
                .filter(VideoTask.status == "FAILED", VideoTask.failed_at >= since)
                .scalar()
                or 0
            )
        return counters
    except Exception as e:
        logger.warning(f"按状态统计任务失败（可能未迁移）：{e}")
        return {}
    finally:
        db.close()

def list_tasks_for_user(
    user_id: int | None,
    *,
    is_admin: bool = False,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[dict], int]:
    """按用户查任务列表（走 DB 索引，不扫文件）。

    返回 (当前页, 总数)。排序按 updated_at 倒序（没有则用 created_at）。

    归属规则与接口层一致：
      - 有 user_id：必须等于当前用户
      - 无 user_id：仅管理员可见（历史数据）
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import or_

    db = next(get_db())
    try:
        q = db.query(VideoTask).filter(VideoTask.deleted_at.is_(None))

        if user_id is not None:
            if is_admin:
                cond = or_(VideoTask.user_id == user_id, VideoTask.user_id.is_(None))
            else:
                cond = VideoTask.user_id == user_id
            q = q.filter(cond)
        elif not is_admin:
            return [], 0

        if status:
            q = q.filter(VideoTask.status == status.strip().upper())

        total = q.with_entities(sa_func.count(VideoTask.id)).scalar() or 0

        rows = (
            q.order_by(
                sa_func.coalesce(VideoTask.updated_at, VideoTask.created_at).desc()
            )
            .limit(limit)
            .offset(offset)
            .all()
        )

        items = []
        for row in rows:
            updated = row.updated_at or row.created_at
            items.append(
                {
                    "task_id": row.task_id,
                    "status": (row.status or "").upper() or "PENDING",
                    "message": row.error,
                    "platform": row.platform,
                    "video_id": row.video_id,
                    "updated_at": updated.isoformat() if updated else None,
                    "failed_at": row.failed_at.isoformat() if row.failed_at else None,
                }
            )
        return items, int(total)
    except Exception as e:
        logger.warning(f"按用户查询任务列表失败: {e}")
        return [], 0
    finally:
        db.close()
