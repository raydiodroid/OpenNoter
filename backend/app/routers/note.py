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
    get_task_by_id,
    get_task_ids_by_video,
    delete_task_by_task_id,
)
from app.db.models.users import User
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.services.auth import get_optional_user
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
UPLOAD_DIR = "uploads"
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


def _task_owner(task_id: str):
    """从状态文件里读出任务归属，老任务没有 user_id 时返回 None。"""
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    if not os.path.exists(status_path):
        return None
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            return json.load(f).get("user_id")
    except Exception:
        return None


def _purge_task(task_id: str) -> tuple[list[str], bool]:
    """清掉一个任务的落盘文件、向量索引和数据库记录。

    返回 (被删掉的文件列表, 数据库里是否有记录)。
    """
    removed = []
    for suffix in (".status.json", ".json", "_transcript.json"):
        path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}{suffix}")
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(path)
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
    owner = _task_owner(task_id)
    if owner is not None and user is not None and owner != user.id:
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
        owner = _task_owner(task_id)
        if owner is not None and user is not None and owner != user.id:
            continue
        _purge_task(task_id)
        deleted.append(task_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return R.success({"task_ids": deleted})


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_location = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_location, "wb+") as f:
        f.write(await file.read())

    # 假设你静态目录挂载了 /uploads
    return R.success({"url": f"/uploads/{file.filename}"})


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
    # 模型/供应商当场校验。不在这里校验的话，错的 provider_id 会先返回 200 + task_id，
    # 任务在后台才失败，调用方只能轮询一次才发现「供应商不存在/未保存」。
    provider_id, model_name = resolve_model_selection(provider_id, model_name)

    if task_id:
        # 传了 task_id 说明是重试
        logger.info(f"重试模式，复用已有 task_id={task_id}")
    else:
        task_id = str(uuid.uuid4())

    # 统一先写入 PENDING，表示已进入队列等待串行执行；顺带记下任务归属
    NoteGenerator()._update_status(
        task_id, TaskStatus.PENDING, user_id=user.id if user else None
    )

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
    return R.success({"task_id": task_id, "platform": platform})


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

        # 归属校验：老任务没有 user_id 时放行，避免把历史结果锁死
        owner = status_content.get("user_id")
        if owner is not None and user is not None and owner != user.id:
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

    # 没有状态文件，但有结果
    if os.path.exists(result_path):
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
