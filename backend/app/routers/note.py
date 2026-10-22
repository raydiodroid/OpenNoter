# app/routers/note.py
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends
from pydantic import BaseModel, validator, field_validator, model_validator
from dataclasses import asdict

from app.db.video_task_dao import (
    get_task_by_id,
    get_task_ids_by_video,
    delete_task_by_task_id,
)
from app.db.models.users import User
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.services.auth import auth_enforced, get_optional_user
from app.services.model_selection import resolve_model_selection
from app.services.note import NoteGenerator, logger, transcript_cache_path
from app.services.task_serial_executor import task_serial_executor
from app.services.vector_store import VectorStoreManager
from app.utils.response import ResponseWrapper as R
from app.utils.url_parser import detect_platform, normalize_video_url
from app.utils.url_safety import UnsafeUrlError, assert_safe_url
from app.validators.video_url_validator import is_supported_video_url
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
from app.enmus.task_status_enums import TaskStatus
from app.utils.upload_paths import (
    max_upload_bytes,
    resolve_under_uploads,
    sanitize_store_name,
    uploads_dir,
)

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

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
        url = str(v)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            # 是网络链接，继续用原有平台校验
            if not is_supported_video_url(url):
                raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)

        return v


class CreateNoteRequest(BaseModel):
    """POST /api/notes 的请求体：只要给个链接就能出笔记。

    provider_id / model_name 留空时自动挑选；platform 留空时按链接自动识别。
    """

    url: str
    platform: Optional[str] = None
    quality: DownloadQuality = DownloadQuality.fast
    provider_id: Optional[str] = None
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

    @field_validator("url")
    def validate_supported_url(cls, v):
        if not is_supported_video_url(str(v)):
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        return v


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = str(uploads_dir())
# image_proxy 允许的最大重定向跳数（每跳都会重新做一次 SSRF 校验）
MAX_IMAGE_REDIRECTS = 3


def save_note_to_file(task_id: str, note):
    os.makedirs(NOTE_OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json"), "w", encoding="utf-8") as f:
        json.dump(asdict(note), f, ensure_ascii=False, indent=2)


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
        "language": transcript.get("language") or "zh",
        "full_text": full_text,
        "segments": cleaned_segments,
    }

    target = transcript_cache_path(task_id, video_url, quality)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"已写入客户端预取字幕缓存: {target} ({len(cleaned_segments)} 段)")


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, video_understanding: bool = False,
                  video_interval=0, grid_size=[]
                  ):

    if not model_name or not provider_id:
        raise HTTPException(status_code=400, detail="请选择模型和提供者")

    def _execute_note_task():
        return NoteGenerator().generate(
            video_url=video_url,
            platform=platform,
            quality=quality,
            task_id=task_id,
            model_name=model_name,
            provider_id=provider_id,
            link=link,
            _format=_format,
            style=style,
            extras=extras,
            screenshot=screenshot,
            video_understanding=video_understanding,
            video_interval=video_interval,
            grid_size=grid_size,
        )

    logger.info(f"任务进入执行队列 (task_id={task_id})")
    note = task_serial_executor.run(_execute_note_task)
    logger.info(f"Note generated: {task_id}")
    if not note or not note.markdown:
        logger.warning(f"任务 {task_id} 执行失败，跳过保存")
        return
    save_note_to_file(task_id, note)

    # 自动建立向量索引（用于 AI 问答），失败不影响笔记生成
    try:
        from app.services.vector_store import VectorStoreManager
        VectorStoreManager().index_task(task_id)
    except Exception as e:
        logger.warning(f"向量索引失败（不影响笔记）: {e}")


def _ownerless_accessible(user: Optional[User]) -> bool:
    """无归属任务是否可被当前身份访问。

    强制认证关闭时，匿名可创建任务（user_id=None）；若此处一律拒匿名，
    会出现「能创建却立刻 403 轮询」——前端再吞错就会永远「生成中」。
    因此：未强制认证 + 匿名 → 允许凭 task_id 访问无归属任务。
    强制认证开启后新任务必有归属；无归属只剩历史数据，仅管理员可碰。
    已登录的非管理员仍不能操作无归属任务（避免任意登录用户清历史）。
    """
    if user and user.is_superuser:
        return True
    if user is None and not auth_enforced():
        return True
    return False


def _can_access_task(task_id: str, user: Optional[User]) -> bool:
    """统一的「当前用户能否操作该任务」判定。

    规则：
      - 有归属：必须登录且 id 匹配；管理员不例外（管理员用管理接口，不走这条）
      - 无归属：见 `_ownerless_accessible`
    """
    owner = _task_owner(task_id)
    if owner is not None:
        return bool(user) and owner == user.id
    return _ownerless_accessible(user)


def _can_access_task_from_payload(payload: dict, user: Optional[User]) -> bool:
    """同 _can_access_task，但直接吃已读出的状态 payload（避免再读一次文件）。"""
    owner = payload.get("user_id")
    if owner is not None:
        return bool(user) and owner == user.id
    return _ownerless_accessible(user)


def _task_owner(task_id: str):
    """读任务归属：先状态文件，再 DB；都没有则视为无归属。"""
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                owner = json.load(f).get("user_id")
            if owner is not None:
                return owner
        except Exception:
            pass
    try:
        row = get_task_by_id(task_id)
        if row is not None and getattr(row, "user_id", None) is not None:
            return row.user_id
    except Exception:
        pass
    return None


def _purge_task(task_id: str) -> tuple[list[str], bool]:
    """清掉一个任务的落盘文件、指纹缓存、向量索引和数据库记录。

    返回 (被删掉的文件列表, 数据库里是否有记录)。
    除 `.status.json` / `.json` 外，还按 NoteCachePaths 的命名清掉
    `{task_id}_{fingerprint}_audio|transcript|markdown` 以及 legacy 无指纹文件，
    避免删除后残留转写/音频缓存导致配额与磁盘偏高。
    """
    removed = []
    note_dir = Path(NOTE_OUTPUT_DIR)
    exact_names = (
        f"{task_id}.status.json",
        f"{task_id}.json",
        f"{task_id}_transcript.json",
        f"{task_id}_audio.json",
        f"{task_id}_markdown.md",
    )
    candidates: list[Path] = [note_dir / name for name in exact_names]
    if note_dir.exists():
        for pattern in (
            f"{task_id}_*_audio.json",
            f"{task_id}_*_transcript.json",
            f"{task_id}_*_markdown.md",
            f"{task_id}_*_markdown.status.json",
        ):
            candidates.extend(note_dir.glob(pattern))

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            try:
                path.unlink()
                removed.append(key)
            except OSError as e:
                logger.warning(f"删除任务文件失败 {path}: {e}")

    try:
        VectorStoreManager().delete_index(task_id)
    except Exception as e:
        logger.warning(f"删除向量索引失败 (task_id={task_id}): {e}")

    had_row = delete_task_by_task_id(task_id)
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
    _ = user
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
    except HTTPException:
        raise
    except Exception as e:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(f"上传失败: {e}")
        raise HTTPException(status_code=500, detail="上传失败")

    return R.success({"url": f"/uploads/{store_name}", "filename": store_name, "size": written})


def _transcriber_readiness_gate():
    """转写引擎就绪门禁：模型没下好就别开任务。

    本地转写引擎（fast-whisper / mlx-whisper）必须等模型下载完才能跑视频，
    否则任务会卡在首次下载（慢 / OOM / 截断），用户只看到一个静默失败的任务。
    客户端已抓好字幕（prefetched_transcript）则不需要转写，可以跳过检查。

    就绪时返回 None，否则返回一个错误响应。
    """
    from app.services.transcriber_config_manager import TranscriberConfigManager
    readiness = TranscriberConfigManager().is_model_ready()
    if readiness["ready"]:
        return None
    logger.warning(f"拒绝创建笔记任务：{readiness['reason']}")
    return R.error(
        msg=readiness["reason"],
        code=NoteErrorEnum.TRANSCRIBER_NOT_READY.code,
        data={
            "reason": "transcriber_model_not_ready",
            "transcriber_type": readiness["transcriber_type"],
            "model_size": readiness["model_size"],
            "downloading": readiness["downloading"],
        },
    )


def _submit_note_task(background_tasks: BackgroundTasks, *, video_url: str, platform: str,
                      quality: DownloadQuality, screenshot: bool, link: bool,
                      model_name: Optional[str], provider_id: Optional[str],
                      task_id: Optional[str], fmt: list, style: Optional[str],
                      extras: Optional[str], video_understanding: bool,
                      video_interval: int, grid_size: list,
                      prefetched_transcript: Optional[dict], user) -> str:
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

    # 先快速拦一次（无锁），避免无谓的模型解析；真正防并发突破在 begin_submit。
    try:
        check_submit_quota(user)
    except QuotaExceeded as exc:
        return _quota_error(exc)

    # 模型/供应商当场校验（无副作用）。不在这里校验的话，错的 provider_id
    # 会先返回 200 + task_id，任务在后台才失败。
    provider_id, model_name = resolve_model_selection(provider_id, model_name)

    if task_id:
        logger.info(f"重试模式，复用已有 task_id={task_id}")
    else:
        task_id = str(uuid.uuid4())

    # 配额校验与写 PENDING 必须同临界区，否则并发双检都能通过并突破上限。
    try:
        tid = task_id

        def _write_pending():
            NoteGenerator()._update_status(
                tid,
                TaskStatus.PENDING,
                user_id=user.id if user else None,
                platform=platform,
                video_id=str(video_url),
            )

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
                              extras, video_understanding, video_interval, grid_size)
    return task_id


@router.post("/generate_note", include_in_schema=False)
def generate_note(data: VideoRequest, background_tasks: BackgroundTasks,
                  user: User | None = Depends(get_optional_user)):
    # 就绪门禁（客户端已抓好字幕则不需要转写，跳过检查）
    if not data.prefetched_transcript:
        gate = _transcriber_readiness_gate()
        if gate is not None:
            return gate

    task_id = _submit_note_task(
        background_tasks,
        video_url=data.video_url,
        platform=data.platform,
        quality=data.quality,
        screenshot=bool(data.screenshot),
        link=bool(data.link),
        model_name=data.model_name,
        provider_id=data.provider_id,
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

    # 客户端已抓好字幕则不需要转写，跳过就绪门禁
    if not data.prefetched_transcript:
        gate = _transcriber_readiness_gate()
        if gate is not None:
            return gate

    task_id = _submit_note_task(
        background_tasks,
        video_url=data.url,
        platform=platform,
        quality=data.quality,
        screenshot=bool(data.screenshot),
        link=bool(data.link),
        model_name=data.model_name,
        provider_id=data.provider_id,
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

    # ---- 首选：查 DB（有 user_id / updated_at 索引，O(log n)）----
    # 文件扫描是 O(全部状态文件)：实测 5000 个文件单次 137ms、20000 个 715ms。
    # 多用户下每个用户每次刷新都付这个成本，不可接受。
    try:
        from app.db.video_task_dao import list_tasks_for_user

        db_items, db_total = list_tasks_for_user(
            user.id if user else None,
            is_admin=is_admin,
            limit=limit,
            offset=offset,
            status=wanted_status,
        )
        # DB 有数据就直接返回；为空则回退到扫文件——
        # 兼容「迁移前就已存在、还没进 DB」的历史任务。
        if db_total > 0:
            return R.success(
                {"items": db_items, "total": db_total, "limit": limit, "offset": offset,
                 "source": "db"}
            )
    except Exception as exc:
        logger.warning(f"DB 列表查询失败，回退到文件扫描：{exc}")

    # ---- 回退：扫文件（历史数据 / DB 未迁移时）----
    note_dir = Path(NOTE_OUTPUT_DIR)
    if not note_dir.exists():
        return R.success({"items": [], "total": 0, "limit": limit, "offset": offset})

    items: list[dict] = []
    scan_errors = 0

    try:
        # 排除 `{task_id}_markdown.status.json` 这类缓存残留：
        # 它们会被误认为独立任务，让列表出现重复项。
        status_files = [
            p
            for p in note_dir.glob("*.status.json")
            if not any(
                p.name[: -len(".status.json")].lower().endswith(sfx)
                for sfx in ("_markdown", "_audio", "_transcript", "_meta")
            )
        ]
    except OSError as exc:
        logger.warning(f"扫描任务目录失败: {exc}")
        return R.success({"items": [], "total": 0, "limit": limit, "offset": offset})

    for path in status_files:
        try:
            st = path.stat()
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("not a dict")
        except Exception:
            scan_errors += 1
            continue

        owner = payload.get("user_id")
        if owner is not None:
            if user is None or owner != user.id:
                continue
        elif not is_admin:
            # 无归属的历史数据：只让管理员看到
            continue

        task_status = str(payload.get("status") or "").upper()
        if wanted_status and task_status != wanted_status:
            continue

        task_id = path.name[: -len(".status.json")]
        # 只有成功的任务才有正文；列表里给个标记，前端点开再拉全文
        result_path = path.with_name(f"{task_id}.json")
        items.append(
            {
                "task_id": task_id,
                "status": task_status,
                "message": payload.get("message") or None,
                "has_result": result_path.exists(),
                "updated_at": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat(),
                "failed_at": payload.get("failed_at"),
            }
        )

    items.sort(key=lambda x: x["updated_at"], reverse=True)
    total = len(items)
    page = items[offset : offset + limit]

    if scan_errors:
        logger.warning(f"列表扫描跳过 {scan_errors} 个无法解析的状态文件")

    return R.success(
        {
            "items": page,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


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
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    result_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json")

    # 优先读状态文件
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status_content = json.load(f)

        # 归属校验：与删除/列表用同一套规则（见 _can_access_task）。
        if not _can_access_task_from_payload(status_content, user):
            raise HTTPException(status_code=403, detail="无权访问该任务")

        status = status_content.get("status")
        message = status_content.get("message", "")

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    result_content = json.load(rf)
                return R.success({
                    "status": status,
                    "result": result_content,
                    "message": message,
                    "task_id": task_id
                })
            else:
                # 理论上不会出现，保险处理
                return R.success({
                    "status": TaskStatus.PENDING.value,
                    "message": "任务完成，但结果文件未找到",
                    "task_id": task_id
                })

        if status == TaskStatus.FAILED.value:
            # 任务失败是一次「查询到任务状态」的成功结果，不是接口本身出错。
            # 之前这里走 R.error，客户端拿到非 0 的 code 只能当异常处理，
            # 反而读不到 status=FAILED，失败任务会一直显示成「生成中」。
            return R.success({
                "status": status,
                "message": message or "任务失败",
                "task_id": task_id
            })

        # 处理中状态
        return R.success({
            "status": status,
            "message": message,
            "task_id": task_id
        })

    # 没有状态文件，但有结果 —— 同样做归属校验，堵住「仅凭 UUID 读笔记」旁路
    if os.path.exists(result_path):
        if not _can_access_task(task_id, user):
            raise HTTPException(status_code=403, detail="无权访问该任务")
        with open(result_path, "r", encoding="utf-8") as f:
            result_content = json.load(f)
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "result": result_content,
            "task_id": task_id
        })

    # 文件和数据库都没有 → 这个 task_id 根本不存在，别装作「排队中」
    if get_task_by_id(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if not _can_access_task(task_id, user):
        raise HTTPException(status_code=403, detail="无权访问该任务")

    # 什么都没有，默认PENDING
    return R.success({
        "status": TaskStatus.PENDING.value,
        "message": "任务排队中",
        "task_id": task_id
    })


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

    current = url
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            for _ in range(MAX_IMAGE_REDIRECTS + 1):
                assert_safe_url(current)
                resp = await client.get(current, headers=headers)

                if resp.is_redirect:
                    location = resp.headers.get("Location")
                    if not location:
                        raise HTTPException(status_code=502, detail="重定向缺少 Location")
                    current = httpx.URL(current).join(location)
                    continue

                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return StreamingResponse(
                    resp.aiter_bytes(),
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=86400",  #  缓存一天
                        "Content-Type": content_type,
                    }
                )
        raise HTTPException(status_code=502, detail="重定向次数过多")
    except UnsafeUrlError as e:
        logger.warning(f"image_proxy 拒绝请求: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
