# app/routers/note.py
import json
import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends
from pydantic import BaseModel, validator, field_validator, model_validator
from dataclasses import asdict

from app.db.video_task_dao import (
    get_task_ids_by_video,
)
from app.db.models.users import User
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.services.auth import auth_enforced, get_optional_user
from app.services.model_selection import resolve_model_selection
from app.utils.logger import get_logger
from app.storage.task_files import LocalTaskStorage, cache_fingerprint

logger = get_logger(__name__)
from app.services.task_serial_executor import task_serial_executor
from app.services.vector_store import VectorStoreManager
from app.utils.response import ResponseWrapper as R
from app.utils.url_parser import detect_platform, normalize_video_url
from app.utils.url_safety import UnsafeUrlError, safe_get
from app.validators.video_url_validator import is_supported_video_url
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from app.utils.note_paths import (
    UnsafeTaskId,
    is_safe_task_id,
    validate_task_id,
)
from app.utils.upload_paths import (
    is_upload_ref,
    max_upload_bytes,
    resolve_under_uploads,
    safe_upload_rel,
    sanitize_store_name,
    set_upload_owner,
    uploads_dir,
)

router = APIRouter(dependencies=[Depends(get_optional_user)])


class TaskDeleteRequest(BaseModel):
    """删除任务的请求体（兼容旧调用方；新调用方直接用 DELETE /api/tasks/{task_id}）。"""

    task_id: Optional[str] = None
    video_id: Optional[str] = None
    platform: Optional[str] = None


class VideoRequest(BaseModel):
    video_url: str
    platform: str
    quality: DownloadQuality = DownloadQuality.fast
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    # provider_id / model_name 留空时自动挑选「已启用供应商 + 其下已启用模型」
    asr_provider_id: Optional[str] = None
    model_name: Optional[str] = None
    provider_id: Optional[str] = None
    task_id: Optional[str] = None
    format: Optional[list] = []
    style: str = None
    extras: Optional[str]=None
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []
    # 客户端（如浏览器插件）已经在用户浏览器里抓到字幕，直接传给后端复用，
    # 跳过 download_subtitles 和音频转写。形如：
    #   {"language": "zh", "full_text": "...", "segments": [{"start","end","text"}, ...]}
    prefetched_transcript: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_url(cls, data):
        # 稍后再看/收藏夹/带追踪参数的 B 站链接先规范化成标准 /video/BVxxx 形式，
        # 后续校验和 yt-dlp 下载拿到的都是干净链接
        if isinstance(data, dict) and data.get("platform") == "bilibili" and data.get("video_url"):
            data["video_url"] = normalize_video_url(str(data["video_url"]))
        return data

    @field_validator("video_url")
    def validate_supported_url(cls, v):
        """视频链接必须是受支持平台的 http(s) 链接。

        ## 为什么不能「scheme 不是 http(s) 就跳过校验」

        旧实现是：

            if parsed.scheme in ("http", "https"):
                if not is_supported_video_url(url): raise ...

        也就是说**只校验「看起来像链接」的输入**。于是：

            "x http://169.254.169.254/"   → scheme 为空 → 原样放行
            "file:///etc/passwd"          → scheme=file → 原样放行

        这些值随后流到下载器。快手那条链尤其危险：`_URL_RE` 会把
        `"x http://169.254.169.254/"` 里的 URL 抠出来还原成
        `http://169.254.169.254/`，再 `requests.get(allow_redirects=True)`
        零校验地发出去 —— 构成盲 SSRF / 内网端口探测。

        ## 正确语义

        「不是 http(s)」不是「不用校验」，而是**本身就不合法**。所以直接拒绝，
        不用再走白名单。这与 `CreateNoteRequest`（`/api/notes`）的口径一致。
        """
        url = str(v)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise NoteError(
                code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message,
            )
        if not is_supported_video_url(url):
            raise NoteError(
                code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message,
            )
        return v


class CreateNoteRequest(BaseModel):
    """POST /api/notes 的请求体：只要给个链接就能出笔记。

    provider_id / model_name 留空时自动挑选；platform 留空时按链接自动识别。
    """

    url: str
    platform: Optional[str] = None
    quality: DownloadQuality = DownloadQuality.fast
    provider_id: Optional[str] = None
    asr_provider_id: Optional[str] = None
    model_name: Optional[str] = None
    style: Optional[str] = None
    format: Optional[list] = []
    extras: Optional[str] = None
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []
    # 重试时带上原 task_id，复用同一个任务槽位
    task_id: Optional[str] = None
    # 客户端（如浏览器插件）已经在用户浏览器里抓好字幕，直接传给后端复用
    prefetched_transcript: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_url(cls, data):
        if not isinstance(data, dict):
            return data
        # 兼容旧字段名 video_url
        if not data.get("url") and data.get("video_url"):
            data["url"] = data["video_url"]
        if data.get("url"):
            data["url"] = normalize_video_url(str(data["url"]))
        return data

    @model_validator(mode="after")
    def validate_source(self):
        """按来源类型分流校验。

        **为什么不再无条件走视频域名白名单**：上传接口返回的是相对路径
        ``/uploads/<uuid>.ext``，scheme 为空，会被 ``is_supported_video_url``
        的第一关（scheme 必须是 http/https）直接判死 —— 这正是"本地上传在
        ``POST /api/notes`` 上完全不可用"的原因。

        分流规则：
          - ``platform=local`` 或路径是 ``/uploads/`` 引用 → 走 uploads 沙箱校验；
          - 其余仍必须通过 ``is_supported_video_url``（含 SSRF 黑名单）。

        注意：**不能**为了修本地文件而让任意非 http(s) 字符串过关 ——
        ``/etc/passwd``、``not-a-url`` 这类必须在入口就被拒。
        """
        raw = str(self.url)
        is_local = (self.platform == "local") or is_upload_ref(raw)

        if is_local:
            if not safe_upload_rel(raw):
                raise NoteError(
                    code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                    message="本地视频路径非法（必须是 /uploads/ 下的文件）",
                )
            # platform 留空时补成 local：路由层的 detect_platform 认不出 /uploads/，
            # 不补会让 create_note 走到"无法识别视频平台"的 error 分支。
            if not self.platform:
                self.platform = "local"
            return self

        if not is_supported_video_url(raw):
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        return self


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = str(uploads_dir())


def _assert_upload_owned(url: str, user: Optional[User]) -> None:
    """消费本地上传前校验归属。

    ## 为什么模型校验器里做不了

    `CreateNoteRequest` / `VideoRequest` 的校验器只有字符串，没有请求身份。
    `safe_upload_rel()` 只保证「路径形如 /uploads/<name>」——它挡的是路径穿越，
    不是「这份文件是不是你的」。

    ## 缺了会怎样

    上传文件名是 `uuid4().hex`，不可枚举，所以看起来安全。但 UUID 会通过
    日志、反向代理、分享链接、前端 URL 泄露。`main.py:363` 的静态读取路径
    早就用 `can_read_upload` 校验了归属，**唯独"把这个文件拿去转写"这条
    消费路径没校验** —— 知道文件名就能让服务端处理别人的视频。

    仓库自己在 `upload_paths.py:105-110` 写过「UUID 不是访问控制」，这里是
    那条论证漏掉的调用点。

    ## 无归属的历史文件

    沿用 `can_read_upload` 的同一套语义（见其 docstring 第 4 条），不另立规则，
    避免两处口径分叉。
    """
    from app.utils.upload_paths import can_read_upload, resolve_under_uploads
    from app.services.auth import auth_enforced

    try:
        target = resolve_under_uploads(str(url))
    except ValueError as exc:
        raise NoteError(
            code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
            message=f"本地视频路径非法：{exc}",
        ) from exc

    if not can_read_upload(target, user, auth_enforced=auth_enforced()):
        # 不区分「不存在」和「无权限」，避免用状态码枚举他人文件
        raise NoteError(
            code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
            message="无权访问该本地视频",
        )
# image_proxy 允许的最大重定向跳数（每跳都会重新做一次 SSRF 校验）
MAX_IMAGE_REDIRECTS = 3


def save_note_to_file(task_id: str, note):
    """把笔记 JSON 写到 `{NOTE_OUTPUT_DIR}/{task_id}.json`。

    task_id 走 `note_file()` 沙箱：越界值抛 `UnsafeTaskId`，不会写出目录外。
    调用方（`run_note_task`）负责把它降级成任务失败，而不是让后台线程静默
    写到一个意料之外的路径上。
    """
    from app.services.task_service import TaskService
    TaskService(NOTE_OUTPUT_DIR).save_result(task_id, asdict(note))


def _persist_prefetched_transcript(task_id: str, video_url: str, quality, transcript: dict) -> None:
    """把客户端预取的字幕写到 NoteGenerator 期望的转写缓存文件里。

    NoteGenerator.generate 会优先读该缓存，命中即跳过 download_subtitles 与音频转写流程。
    要求字段：language(可空)/full_text/segments[{start,end,text}]

    缓存文件名必须带 `video_url + quality` 指纹（`transcript_cache_path()`），
    否则会有一个隐蔽的数据错位：前端「重试」复用同一个 task_id，如果用户在重试时
    改了视频链接，老的 `{task_id}_transcript.json` 会被当成新视频的字幕直接命中，
    生成出「A 的字幕 + B 的元信息」的错乱笔记。
    """
    segments = transcript.get("segments") or []
    cleaned_segments = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        cleaned_segments.append({
            "start": float(s.get("start", 0)),
            "end": float(s.get("end", 0)),
            "text": text,
        })
    if not cleaned_segments:
        raise ValueError("prefetched_transcript 没有可用的 segments")

    full_text = transcript.get("full_text") or " ".join(s["text"] for s in cleaned_segments)
    payload = {
        "_transcript_source": "subtitles",
        "language": transcript.get("language") or "zh",
        "full_text": full_text,
        "segments": cleaned_segments,
    }

    suffix = f"_{cache_fingerprint(video_url, quality)}_transcript.json"
    LocalTaskStorage(NOTE_OUTPUT_DIR).write(task_id, payload, suffix)
    logger.info("已写入客户端预取字幕缓存: %s (%s 段)", task_id, len(cleaned_segments))


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, video_understanding: bool = False,
                  video_interval=0, grid_size=None, user_id: int = None, attempt_id: str = None, asr_provider_id: str = None
                  ):

    from app.services.task_service import TaskService
    from app.services.task_worker import submit_note
    state = TaskService(NOTE_OUTPUT_DIR).snapshot(task_id)
    return submit_note(
        task_id, user_id=user_id, attempt_id=attempt_id or (state["attempt_id"] if state else None),
        asr_provider_id=asr_provider_id, root=NOTE_OUTPUT_DIR, video_url=video_url, platform=platform, quality=quality,
        link=link, screenshot=screenshot, model_name=model_name, provider_id=provider_id,
        _format=_format, style=style, extras=extras, video_understanding=video_understanding,
        video_interval=video_interval, grid_size=grid_size,
    )


def _ownerless_accessible(user: Optional[User]) -> bool:
    """无归属任务是否可被当前身份访问。

    ## 现状（匿名关闭后）

    `AUTH_ENFORCE` 默认 True，未登录请求在 `get_optional_user` 那层就被
    401 拦掉，走不到这里。因此本函数实际只需处理「已登录用户」：

    - 管理员 → 可以（清理历史数据、排查问题的出口）
    - 普通用户 → 不可以（否则任何登录用户都能翻到别人的早期任务）

    ## 遗留的兼容分支

    如果部署方显式设了 `OPENNOTER_AUTH_ENFORCE=0`（单机自托管场景），
    匿名创建的任务 user_id 为 None，此时必须允许匿名凭 task_id 读回，
    否则「能创建却读不回来」，前端会永远停在「生成中」。
    这段逻辑保留，但只在显式关闭认证时生效。
    """
    if user and user.is_superuser:
        return True
    if user is None and not auth_enforced():
        return True
    return False


def _can_access_task(task_id: str, user: Optional[User]) -> bool:
    from app.services.task_access import can_access_task
    return can_access_task(task_id, user, NOTE_OUTPUT_DIR)


def _can_access_task_from_payload(payload: dict, user: Optional[User]) -> bool:
    """同 _can_access_task，但直接吃已读出的状态 payload（避免再读一次文件）。"""
    owner = payload.get("user_id")
    if owner is not None:
        return bool(user) and owner == user.id
    return _ownerless_accessible(user)


def _purge_task(task_id: str) -> tuple[list[str], bool]:
    from app.services.quota import _submit_lock
    with _submit_lock:
        return _purge_task_locked(task_id)


def _purge_task_locked(task_id: str) -> tuple[list[str], bool]:
    """清掉一个任务的落盘文件、指纹缓存、向量索引和数据库记录。

    返回 (被删掉的文件列表, 数据库里是否有记录)。
    除 `.status.json` / `.json` 外，还按 NoteCachePaths 的命名清掉
    `{task_id}_{fingerprint}_audio|transcript|markdown` 以及 legacy 无指纹文件，
    避免删除后残留转写/音频缓存导致配额与磁盘偏高。

    ## 为什么每个候选都要过沙箱

    这里的删除是**最终落点**，一旦 task_id 带 `../`，`note_dir / f"{task_id}.json"`
    就会精确命中 NOTE_OUTPUT_DIR 之外的同名文件（例如 `config/downloader.json`）。
    所以：先校验 task_id 格式，再对每个候选路径 `resolve_under()` 断言，
    glob 阶段只用**已校验**的 task_id 组 pattern（`Path.glob` 对绝对路径 pattern
    会抛 NotImplementedError，也不该接受）。
    """
    from app.services.task_service import TaskService, RUNNING
    try:
        state = TaskService(NOTE_OUTPUT_DIR).snapshot(task_id)
    except UnsafeTaskId:
        return [], False
    if task_serial_executor.is_active(task_id) or (state and state["status"] in RUNNING):
        raise HTTPException(status_code=409, detail="任务仍在执行，请等待结束后删除")
    from app.services.task_service import TaskConflict
    try:
        removed, had_row = TaskService(NOTE_OUTPUT_DIR).delete(task_id)
    except TaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        VectorStoreManager().delete_index(task_id)
    except Exception as e:
        logger.warning(f"删除向量索引失败 (task_id={task_id}): {e}")

    return removed, had_row


@router.delete("/tasks/{task_id}")
def delete_task_by_id(task_id: str, user: User | None = Depends(get_optional_user)):
    """删除一个任务。

    删掉：笔记结果 JSON、状态文件、预取字幕缓存、向量索引、数据库记录。
    """
    if not _can_access_task(task_id, user):
        raise HTTPException(status_code=403, detail="无权删除该任务")

    removed, had_row = _purge_task(task_id)
    if not removed and not had_row:
        raise HTTPException(status_code=404, detail="任务不存在")

    logger.info(f"任务已删除: task_id={task_id}, 移除文件 {len(removed)} 个")
    return R.success({"task_id": task_id, "removed": removed})


@router.post('/delete_task', include_in_schema=False)
def delete_task(data: TaskDeleteRequest, user: User | None = Depends(get_optional_user)):
    """旧版删除入口，保留兼容。

    新调用方请用 DELETE /api/tasks/{task_id}：语义更清楚，也不用再记
    video_id + platform —— 生成接口返回的一直是 task_id。
    """
    if not data.task_id and not (data.video_id and data.platform):
        raise HTTPException(status_code=400, detail="需要提供 task_id，或 video_id + platform")

    if data.task_id:
        return delete_task_by_id(data.task_id, user)

    task_ids = get_task_ids_by_video(data.video_id, data.platform)
    if not task_ids:
        raise HTTPException(status_code=404, detail="任务不存在")

    deleted = []
    for task_id in task_ids:
        if not _can_access_task(task_id, user):
            continue
        _purge_task(task_id)
        deleted.append(task_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return R.success({"task_ids": deleted})


@router.post("/upload")
async def upload(file: UploadFile = File(...), user: User | None = Depends(get_optional_user)):
    """本地视频/音频上传。

    - 文件名强制取 basename + UUID 存储，杜绝路径穿越写任意文件
    - 限制扩展名与大小（UPLOAD_MAX_SIZE_MB，默认 500）
    - 返回的 url 仍是 `/uploads/<uuid>.ext`，供 LocalDownloader 解析
    """
    # get_optional_user 在 AUTH_ENFORCE 时已要求登录；此处无需再判
    try:
        store_name = sanitize_store_name(file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = Path(UPLOAD_DIR) / store_name
    # 双检：落盘路径 resolve 后必须仍在 uploads 根内
    try:
        resolve_under_uploads(f"/uploads/{store_name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    limit = max_upload_bytes()
    written = 0
    chunk_size = 1024 * 1024
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if limit and written > limit:
                    f.close()
                    try:
                        dest.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大，上限 {limit // (1024 * 1024)}MB",
                    )
                f.write(chunk)
        set_upload_owner(dest, user.id if user else None)
    except HTTPException:
        raise
    except Exception as e:
        try:
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".owner").unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(f"上传失败: {e}")
        raise HTTPException(status_code=500, detail="上传失败")

    return R.success({"url": f"/uploads/{store_name}", "filename": store_name, "size": written})


def _submit_note_task(background_tasks: BackgroundTasks, *, video_url: str, platform: str,
                      quality: DownloadQuality, screenshot: bool, link: bool,
                      model_name: Optional[str], provider_id: Optional[str],
                      task_id: Optional[str], fmt: list, style: Optional[str],
                      extras: Optional[str], video_understanding: bool,
                      video_interval: int, grid_size: list,
                      prefetched_transcript: Optional[dict], user, asr_provider_id: Optional[str] = None) -> str:
    """校验入参 → 落 PENDING 状态 → 排后台任务 → 返回 task_id。"""
    from app.services.quota import QuotaExceeded, begin_submit, check_submit_quota

    def _quota_error(exc: QuotaExceeded):
        return R.error(
            msg=exc.message,
            code=429,
            data={
                "reason": "quota_exceeded",
                "dimension": exc.dimension,
                "limit": exc.limit,
                "current": exc.current,
                "retry_after": exc.retry_after,
            },
            status_code=429,
        )

    if user is not None:
        try:
            check_submit_quota(user)
        except QuotaExceeded as exc:
            return _quota_error(exc)

    # 模型/供应商当场校验（无副作用）。不在这里校验的话，错的 provider_id
    # 会先返回 200 + task_id，任务在后台才失败。
    #
    # user_id 用于识别 BYOK 的 own: 前缀（用户自有模型不走 providers 表）。
    _sel_user_id = getattr(user, "id", None)
    try:
        provider_id, model_name = resolve_model_selection(
            provider_id, model_name, user_id=_sel_user_id
        )
        from app.services.asr_selection import validate_asr_selection
        from app.services.credential_resolver import require_personal_cookie
        require_personal_cookie(_sel_user_id, platform)
        asr_provider_id = validate_asr_selection(asr_provider_id, _sel_user_id)
    except Exception as _sel_exc:
        # BYOK 的两类专用异常要转成给用户看的 4xx，而不是落到兜底 500
        from app.services.credential_resolver import (
            CredentialBroken,
            CredentialRequired,
        )

        if isinstance(_sel_exc, (CredentialRequired, CredentialBroken, ValueError)):
            return R.error(msg=str(_sel_exc), code=400)
        raise

    retry = bool(task_id)
    if task_id:
        # ---------------------------------------------------------------
        # 「重试」复用已有 task_id：两道校验，缺一不可
        # ---------------------------------------------------------------
        # ① 格式：客户端可控的字符串会参与文件路径拼接（状态文件/笔记/缓存/
        #    向量索引），必须先挡掉 `../` 一类越界值。
        try:
            validate_task_id(task_id)
        except UnsafeTaskId:
            logger.warning(f"拒绝非法 task_id={task_id!r}（用户 {getattr(user, 'id', None)}）")
            raise HTTPException(status_code=400, detail="非法 task_id")

        # ② 归属：只有「这个任务确实属于当前用户」时才允许复用。
        #    缺了这一步，任何登录用户拿别人的 task_id 提交一次任务，就能把
        #    归属改写成自己（历史实现 `_update_status` 会覆盖 user_id），随后
        #    可读/删对方任务。已存在的无归属任务（匿名时代遗留）同样拒绝，
        #    除非当前是管理员 —— 与 `_can_access_task` 的口径保持一致。
        if not _can_access_task(task_id, user) and not (
            user is not None and getattr(user, "is_superuser", False)
        ):
            logger.warning(
                f"拒绝复用他人 task_id={task_id!r}"
                f"（用户 {getattr(user, 'id', None)}）"
            )
            raise HTTPException(status_code=403, detail="无权复用该任务")

        logger.info(f"重试模式，复用已有 task_id={task_id}")
    else:
        task_id = str(uuid.uuid4())

    # 配额校验与写 PENDING 必须同临界区，否则并发双检都能通过并突破上限。
    try:
        tid = task_id
        attempt_id = None

        def _write_pending():
            nonlocal attempt_id
            from app.services.task_service import TaskService, TaskConflict
            request = dict(video_url=str(video_url), platform=platform,
                           quality=getattr(quality, "value", quality), format=fmt,
                           screenshot=screenshot, link=link, model_name=model_name,
                           provider_id=provider_id, asr_provider_id=asr_provider_id, style=style, extras=extras,
                           video_understanding=video_understanding,
                           video_interval=video_interval, grid_size=grid_size)
            if task_serial_executor.is_active(tid):
                raise HTTPException(status_code=409, detail="任务仍在执行")
            try:
                attempt_id = TaskService(NOTE_OUTPUT_DIR).claim(
                    tid, user_id=getattr(user, "id", None), platform=platform,
                    video_url=str(video_url), request=request, retry=retry,
                )
            except TaskConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        begin_submit(user, _write_pending)
    except QuotaExceeded as exc:
        return _quota_error(exc)

    # 客户端已经抓好字幕的话，写到转写缓存文件，NoteGenerator 的 cache-hit 逻辑会直接用上
    if prefetched_transcript:
        try:
            _persist_prefetched_transcript(task_id, video_url, quality, prefetched_transcript)
        except Exception as e:
            logger.warning(f"写入预取字幕失败 (task_id={task_id}): {e}")

    background_tasks.add_task(run_note_task, task_id, video_url, platform, quality, link,
                              screenshot, model_name, provider_id, fmt, style,
                              extras, video_understanding, video_interval, grid_size,
                              getattr(user, "id", None), attempt_id, asr_provider_id)
    return task_id


@router.post("/generate_note", include_in_schema=False)
def generate_note(data: VideoRequest, background_tasks: BackgroundTasks,
                  user: User | None = Depends(get_optional_user)):
    # 本地视频：先校验归属，再谈能不能转写（顺序很重要——无权限的文件
    # 不该因为"转写器没就绪"而泄露它存在）
    if is_upload_ref(str(data.video_url)) or data.platform == "local":
        _assert_upload_owned(str(data.video_url), user)


    task_id = _submit_note_task(
        background_tasks,
        video_url=data.video_url,
        platform=data.platform,
        quality=data.quality,
        screenshot=bool(data.screenshot),
        link=bool(data.link),
        model_name=data.model_name,
        provider_id=data.provider_id,
        asr_provider_id=data.asr_provider_id,
        task_id=data.task_id,
        fmt=data.format,
        style=data.style,
        extras=data.extras,
        video_understanding=bool(data.video_understanding),
        video_interval=data.video_interval,
        grid_size=data.grid_size,
        prefetched_transcript=data.prefetched_transcript,
        user=user,
    )
    # 配额/校验失败时 _submit_note_task 直接返回 R.error（JSONResponse），勿再包进 success
    if not isinstance(task_id, str):
        return task_id
    return R.success({"task_id": task_id})


@router.post("/notes")
def create_note(data: CreateNoteRequest, background_tasks: BackgroundTasks,
                user: User | None = Depends(get_optional_user)):
    """创建笔记任务：给个链接就能出笔记。

    platform 留空按链接自动识别；provider_id / model_name 留空自动挑已启用的那套。
    返回 task_id，用 GET /api/tasks/{task_id} 轮询进度。
    """
    platform = data.platform or detect_platform(data.url)
    if not platform:
        return R.error(
            msg="无法识别视频平台，请显式指定 platform",
            code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
        )

    # 本地视频：校验归属（见 _assert_upload_owned 的说明）
    if platform == "local" or is_upload_ref(str(data.url)):
        _assert_upload_owned(str(data.url), user)


    task_id = _submit_note_task(
        background_tasks,
        video_url=data.url,
        platform=platform,
        quality=data.quality,
        screenshot=bool(data.screenshot),
        link=bool(data.link),
        model_name=data.model_name,
        provider_id=data.provider_id,
        asr_provider_id=data.asr_provider_id,
        task_id=data.task_id,
        fmt=data.format,
        style=data.style,
        extras=data.extras,
        video_understanding=bool(data.video_understanding),
        video_interval=data.video_interval,
        grid_size=data.grid_size,
        prefetched_transcript=data.prefetched_transcript,
        user=user,
    )
    if not isinstance(task_id, str):
        return task_id
    return R.success({"task_id": task_id, "platform": platform})


@router.get("/tasks")
def list_tasks(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    user: User | None = Depends(get_optional_user),
    include_deleted: bool = False,
):
    """列出当前用户的笔记任务（供前端「历史」列表使用）。

    **为什么需要这个接口**：任务列表原先只存在前端 IndexedDB 里，
    导致换浏览器/换设备就看不到历史、清缓存就丢——而笔记文件其实
    一直在服务器上。这里把列表能力补到服务端，让历史真正跟着账号走。

    归属规则（与单条查询保持一致）：
      - 有 user_id 且等于当前用户 → 可见
      - 有 user_id 但不是当前用户 → **不可见**（不泄露他人任务）
      - 没有 user_id（改名前的历史数据）→ 仅管理员可见，
        避免「任何人都能翻到别人早期任务」

    返回按创建时间倒序，只带列表需要的轻量字段（不含 markdown 正文，
    正文由 `/tasks/{id}` 按需拉取）。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    is_admin = bool(user and user.is_superuser)
    wanted_status = (status or "").strip().upper() or None

    from app.db.video_task_dao import list_tasks_for_user
    items, total = list_tasks_for_user(
        getattr(user, "id", None), is_admin=is_admin or (user is None and not auth_enforced()),
        limit=limit, offset=offset, status=wanted_status, note_dir=NOTE_OUTPUT_DIR,
        include_deleted=include_deleted,
    )
    return R.success({"items": items, "total": total, "limit": limit, "offset": offset, "source": "db"})


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: User | None = Depends(get_optional_user)):
    """查询任务状态。

    永远返回 200，任务本身的状态看 data.status（PENDING / DOWNLOADING /
    TRANSCRIBING / ... / SUCCESS / FAILED）。任务失败是「查到了状态」的成功
    结果，不是接口出错。
    """
    return get_task_status(task_id, user)



@router.get("/task_status/{task_id}", include_in_schema=False)
def get_task_status(task_id: str, user: User | None = Depends(get_optional_user)):
    # 读原语必须过沙箱：历史实现直接拼 `{task_id}.json`，配合可植入的
    # status 文件可以让 ACL 通过，再把 NOTE_OUTPUT_DIR 之外的任意 JSON 当
    # 「任务结果」返回给调用方。校验失败按「任务不存在」处理（不泄露路径）。
    try:
        validate_task_id(task_id)
    except UnsafeTaskId:
        raise HTTPException(status_code=400, detail="非法 task_id")

    from app.services.task_service import TaskService
    service = TaskService(NOTE_OUTPUT_DIR)
    state = service.snapshot(task_id)
    if state is not None:
        if state["deleted"]:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not _can_access_task_from_payload(state, user):
            raise HTTPException(status_code=403, detail="无权访问该任务")
        result = None
        status = state["status"] or ("SUCCESS" if service.storage.read(task_id) is not None else "FAILED")
        message = state["message"]
        if status == "SUCCESS":
            result = service.storage.read(task_id)
            if result is None:
                service.fail_missing_result(task_id, state["attempt_id"])
                status, message = "FAILED", "笔记结果文件缺失或损坏，请重新生成"
        return R.success(dict(task_id=task_id, status=status, message=message,
                              result=result, request=state["request"], attempt_id=state["attempt_id"]))

    # Read-only compatibility for artifacts not adopted at startup.
    legacy = service.storage.read(task_id, ".status.json")
    result = service.storage.read(task_id)
    if legacy is not None:
        if not _can_access_task_from_payload(legacy, user):
            raise HTTPException(status_code=403, detail="无权访问该任务")
        from app.services.task_service import RUNNING, TERMINAL
        status = legacy.get("status")
        message = legacy.get("message") or ""
        if status not in RUNNING | TERMINAL or (status == "SUCCESS" and result is None):
            status, message = "FAILED", "笔记结果或状态文件损坏，请重新生成"
        return R.success(dict(task_id=task_id, status=status, message=message,
                              result=result if status == "SUCCESS" else None))
    if result is not None:
        if not _ownerless_accessible(user):
            raise HTTPException(status_code=403, detail="无权访问该任务")
        return R.success(dict(task_id=task_id, status="SUCCESS", result=result))
    raise HTTPException(status_code=404, detail="任务不存在")


@router.get("/image_proxy")
async def image_proxy(request: Request, url: str):
    """代理抓取远程图片。

    这是个「拿调用方给的 URL 去服务器发请求」的接口，必须防 SSRF：
      - 只允许 http/https
      - 目标不能解析到内网 / 回环 / 链路本地地址（含云元数据 169.254.169.254）
      - 域名必须在图片 CDN 白名单内
      - 重定向逐跳校验，防止用 302 跳到内网
    """
    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    def fetch_image():
        allowed = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}
        limit = 10 * 1024 * 1024
        with safe_get(url, timeout=(5, 10), stream=True, headers=headers,
                      max_redirects=MAX_IMAGE_REDIRECTS) as response:
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="图片获取失败")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in allowed:
                raise HTTPException(status_code=415, detail="只允许常用位图图片格式")
            content = bytearray()
            for chunk in response.iter_content(65536):
                if len(content) + len(chunk) > limit:
                    raise HTTPException(status_code=413, detail="图片超过 10MB 限制")
                content.extend(chunk)
            return bytes(content), content_type

    try:
        content, content_type = await run_in_threadpool(fetch_image)
        return Response(content, media_type=content_type, headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        })
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("图片代理请求失败: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="图片获取失败") from exc
