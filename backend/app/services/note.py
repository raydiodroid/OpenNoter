import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Union, Any

from pydantic import HttpUrl
from dotenv import load_dotenv

from app.downloaders.base import Downloader
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
    is_retryable,
)
from app.downloaders.limits import get_download_limits
from app.db.video_task_dao import delete_task_by_video, insert_video_task, update_task_status
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.enmus.task_status_enums import TaskStatus
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.gpt.base import GPT
from app.gpt.gpt_factory import GPTFactory
from app.models.audio_model import AudioDownloadResult
from app.models.gpt_model import GPTSource
from app.models.model_config import ModelConfig
from app.models.notes_model import AudioDownloadResult, NoteResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.constant import get_downloader
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.transcriber.transcriber_provider import get_transcriber, _transcribers
from app.utils.note_helper import replace_content_markers, prepend_source_link
from app.utils.note_paths import UnsafeTaskId, note_file, note_named_file
from app.utils.screenshot_marker import extract_screenshot_timestamps
from app.utils.video_helper import generate_screenshot
from app.utils.video_reader import VideoReader

# ------------------ 环境变量与全局配置 ------------------

# 从 .env 文件中加载环境变量
load_dotenv()

# 后端 API 地址与端口（若有需要可以在代码其他部分使用 BACKEND_BASE_URL）
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
BACKEND_PORT = os.getenv("BACKEND_PORT", "8483")
BACKEND_BASE_URL = f"{API_BASE_URL}:{BACKEND_PORT}"

# 输出目录（用于缓存音频、转写、Markdown 文件，以及存储截图）
NOTE_OUTPUT_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_OUTPUT_DIR = os.getenv("OUT_DIR", "./static/screenshots")
# 图片基础 URL（用于生成 Markdown 中的图片链接，需前端静态目录对应）
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "/static/screenshots")

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _sign_static_url(url: str) -> str:
    """给 `/static/` 下的 URL 补签名令牌（F9）。

    Tauri / 浏览器扩展里的 ``<img>`` 带不上 Cookie 或 API-Key，只能靠 URL 上的
    令牌通过 ``/static/`` 的鉴权。签名助手由 security 组提供
    （``app/utils/static_signing.py`` 的 ``sign_static_url``）。

    该模块由并行施工引入，**可能尚未落地**：导入失败时原样返回，保持改动前的
    行为；签名本身出错也不能让整段截图被外层 except 吞掉（笔记会丢图）。
    """
    try:
        from app.utils.static_signing import sign_static_url
    except ImportError:
        return url
    try:
        return sign_static_url(url)
    except Exception as exc:  # noqa: BLE001 - 签名失败不该影响笔记生成
        logger.warning(f"静态资源签名失败，退回未签名 URL：{exc}")
        return url


# ------------------ 缓存文件命名（task_id + 链接/质量指纹） ------------------

# 历史 bug：缓存文件只按 task_id 命名。前端「重试」会复用同一个 task_id 提交
# （`NoteForm.tsx` 传 `task_id: currentTask.id`），如果用户重试时改了链接或质量，
# 就会命中上一个视频的音频缓存，转写/总结出来的内容与当前链接完全无关。
# 这里把「链接 + 质量」压成短哈希放进文件名，从根上区分不同输入。
_CACHE_FINGERPRINT_LEN = 12


def cache_fingerprint(video_url: Union[str, HttpUrl], quality) -> str:
    """把「视频链接 + 音频质量」压成 12 位短哈希，用作缓存文件指纹。

    只取短哈希而不是完整 URL：文件名不能太长，也不能带 `://`、`?` 这些
    Windows 上非法的字符。
    """
    quality_value = getattr(quality, "value", quality)
    raw = f"{video_url}|{quality_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_CACHE_FINGERPRINT_LEN]


@dataclass(frozen=True)
class NoteCachePaths:
    """一个任务的缓存文件路径。

    - `audio` / `transcript` / `markdown`：带「链接 + 质量」指纹的新命名，**只写这里**
    - `legacy_*`：不带指纹的历史命名，仅在新路径不存在时用于读取，
      让老任务的缓存还能命中（写永远只写新路径）
    """

    task_id: str
    fingerprint: str
    audio: Path
    transcript: Path
    markdown: Path
    legacy_audio: Path
    legacy_transcript: Path
    legacy_markdown: Path

    @classmethod
    def build(cls, task_id: Optional[str], video_url: Union[str, HttpUrl], quality) -> "NoteCachePaths":
        safe_task_id = str(task_id or "anonymous")
        fingerprint = cache_fingerprint(video_url, quality)
        # 所有缓存路径都走沙箱：task_id 越界时抛 UnsafeTaskId，
        # 而不是拼出一个指向 NOTE_OUTPUT_DIR 之外的路径。
        # 带指纹的才是「写」路径；legacy_* 只用于读旧任务缓存。
        # 用 note_named_file（文件名已含后缀），不能再用 note_file 追加后缀。
        return cls(
            task_id=safe_task_id,
            fingerprint=fingerprint,
            audio=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_{fingerprint}_audio.json"),
            transcript=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_{fingerprint}_transcript.json"),
            markdown=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_{fingerprint}_markdown.md"),
            legacy_audio=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_audio.json"),
            legacy_transcript=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_transcript.json"),
            legacy_markdown=note_named_file(NOTE_OUTPUT_DIR, f"{safe_task_id}_markdown.md"),
        )


def _first_existing(*paths: Path) -> Optional[Path]:
    """返回第一个存在的缓存文件；都不存在时返回 None。"""
    for path in paths:
        if path.exists():
            return path
    return None


def transcript_cache_path(task_id: Optional[str], video_url: Union[str, HttpUrl], quality) -> Path:
    """转写缓存的**新**路径（带指纹）。

    给 `app/routers/note.py` 的客户端预取字幕落盘用：那边（`_persist_prefetched_transcript`）
    目前仍写老命名 `{task_id}_transcript.json`，只能靠 `NoteCachePaths.legacy_transcript`
    兜底读到。这留下一个残余风险——「用视频 A 提交并写入预取字幕 → 重试时换成视频 B」
    会命中 A 的老 key 字幕。彻底修法就在那边改成写本函数返回的路径，
    但 `routers/note.py` 不在本次改动范围内，所以这里只提供入口 + 记录风险。
    """
    return NoteCachePaths.build(task_id, video_url, quality).transcript


class NoteGenerator:
    """
    NoteGenerator 用于执行视频/音频下载、转写、GPT 生成笔记、插入截图/链接、
    以及将任务信息写入状态文件与数据库等功能。
    """

    def __init__(self, user_id: Optional[int] = None):
        from app.services.transcriber_config_manager import TranscriberConfigManager
        config_manager = TranscriberConfigManager()
        self.model_size: str = config_manager.get_whisper_model_size()
        self.device: Optional[str] = None
        self.transcriber_type: str = config_manager.get_transcriber_type()
        self.transcriber: Transcriber = self._init_transcriber()
        self.video_path: Optional[Path] = None
        self.video_img_urls=[]
        # 下载相关上限（体积 / 时长 / 重试次数）。每次实例化现读，改 env 后新任务即生效。
        self.limits = get_download_limits()
        # BYOK：任务归属用户。用于 `_get_gpt` 解析「用谁的凭据」。
        # None 表示匿名（`AUTH_ENFORCE=0` 的部署），一律走站点凭据。
        self.user_id: Optional[int] = user_id
        logger.info(
            f"NoteGenerator 初始化完成（体积上限 {self.limits.max_size_mb}MB，"
            f"时长上限 {self.limits.max_duration_seconds}s，重试 {self.limits.retry_count} 次，"
            f"user_id={user_id}）"
        )


    # ---------------- 公有方法 ----------------

    def generate(
        self,
        video_url: Union[str, HttpUrl],
        platform: str,
        quality: DownloadQuality = DownloadQuality.medium,
        task_id: Optional[str] = None,
        model_name: Optional[str] = None,
        provider_id: Optional[str] = None,
        link: bool = False,
        screenshot: bool = False,
        _format: Optional[List[str]] = None,
        style: Optional[str] = None,
        extras: Optional[str] = None,
        output_path: Optional[str] = None,
        video_understanding: bool = False,
        video_interval: int = 0,
        grid_size: Optional[List[int]] = None,
    ) -> NoteResult | None:
        """
        主流程：按步骤依次下载、转写、GPT 总结、截图/链接处理、存库、返回 NoteResult。

        :param video_url: 视频或音频链接
        :param platform: 平台名称，对应 SUPPORT_PLATFORM_MAP 中的键
        :param quality: 下载音频的质量枚举
        :param task_id: 用于标识本次任务的唯一 ID，亦用于状态文件和缓存文件命名
        :param model_name: GPT 模型名称
        :param provider_id: 模型供应商 ID
        :param link: 是否在笔记中插入视频片段链接
        :param screenshot: 是否在笔记中替换 Screenshot 标记为图片
        :param _format: 包含 'link' 或 'screenshot' 等字符串的列表，决定后续处理
        :param style: GPT 生成笔记的风格
        :param extras: 额外参数，传递给 GPT
        :param output_path: 下载输出目录（可选）
        :param video_understanding: 是否需要视频拼图理解（生成缩略图）
        :param video_interval: 视频帧截取间隔（秒），仅在 video_understanding 为 True 时生效
        :param grid_size: 生成缩略图时的网格大小，如 [3, 3]
        :return: NoteResult 对象，包含 markdown 文本、转写结果和音频元信息
        """
        if grid_size is None:
            grid_size = []

        # 这两个属性在 `finally` 的清理逻辑里会被读到，先显式初始化：
        # 万一流程在赋值之前就抛异常，`getattr(self, "video_path", None)`
        # 虽然能兜住，但显式初始化让「哪些中间产物属于本任务」更清楚。
        self.video_path = None
        self._cleanup_done = False

        try:
            logger.info(f"开始生成笔记 (task_id={task_id})")
            self._update_status(task_id, TaskStatus.PARSING)

            # 获取下载器与 GPT 实例

            downloader = self._get_downloader(platform)
            gpt = self._get_gpt(model_name, provider_id)

            # 缓存文件路径：文件名带「链接 + 质量」指纹，防止前端重试复用 task_id 时
            # 命中上一个视频的缓存（详见 NoteCachePaths 的说明）
            cache = NoteCachePaths.build(task_id, video_url, quality)

            # 1. 获取字幕/转写：优先缓存 → 平台字幕 → 音频转写
            transcript = self._load_transcript_cache(cache, task_id=task_id)

            # 2/3. 下载 + 取字幕。这一段最容易因网络抖动失败，整段带任务级重试。
            #      holder 用于在多次尝试之间复用已经拿到的字幕，避免重试时重复请求。
            holder: dict = {"transcript": transcript}
            transcript, audio_meta = self._run_with_retry(
                task_id=task_id,
                step="下载与字幕获取",
                status_phase=TaskStatus.DOWNLOADING,
                func=lambda: self._fetch_source(
                    downloader=downloader,
                    video_url=video_url,
                    quality=quality,
                    platform=platform,
                    task_id=task_id,
                    cache=cache,
                    output_path=output_path,
                    screenshot=screenshot,
                    video_understanding=video_understanding,
                    video_interval=video_interval,
                    grid_size=grid_size,
                    holder=holder,
                ),
            )

            # 3. GPT 总结
            markdown = self._summarize_text(
                audio_meta=audio_meta,
                transcript=transcript,
                gpt=gpt,
                markdown_cache_file=cache.markdown,
                task_id=task_id,
                link=link,
                screenshot=screenshot,
                formats=_format or [],
                style=style,
                extras=extras,
                video_img_urls=self.video_img_urls,
            )

            # 4. 截图 & 链接替换
            if _format:
                markdown = self._post_process_markdown(
                    markdown=markdown,
                    video_path=self.video_path,
                    formats=_format,
                    audio_meta=audio_meta,
                    platform=platform,
                    task_id=task_id,
                )

            markdown = prepend_source_link(markdown, str(video_url))

            # 5. 保存记录到数据库
            self._update_status(task_id, TaskStatus.SAVING)
            self._save_metadata(video_id=audio_meta.video_id, platform=platform, task_id=task_id)

            # 6. 生成完成 —— **此处不标 SUCCESS**。
            #    结果文件还没落盘（落盘在 routers/note.py 的 run_note_task 里），
            #    提前标 SUCCESS 会让轮询看到「成功但无结果」，且落盘失败会永久
            #    卡在这个假成功态。改由 save_and_mark_success() 在落盘后标。
            return NoteResult(markdown=markdown, transcript=transcript, audio_meta=audio_meta)

        except Exception as exc:
            # 统一在这里落失败状态：重试期间的中途失败不应该把任务标成 FAILED，
            # 否则前端轮询到 FAILED 就不再等了，重试也就失去意义。
            logger.error(f"生成笔记流程异常 (task_id={task_id})：{exc}", exc_info=True)
            self._handle_exception(task_id, exc)
            return None

        finally:
            # F13：无论成功/失败/异常，都要清理本任务产生的中间媒体。
            #
            # 放在 finally 而不是成功分支，是因为**失败的任务同样会留下大文件**
            # （下载到一半的视频、转写失败的音频）—— 而失败任务往往比成功的多，
            # 磁盘正是被它们吃掉的。
            #
            # 注意：这里只清中间媒体，不动 note_results/ 的缓存 JSON
            # （那是「重试时省一次下载/转写」的资产）。
            try:
                self._cleanup_task_media(task_id)
            except Exception as cleanup_exc:  # noqa: BLE001 - 清理失败不影响任务结果
                logger.warning(f"任务中间文件清理失败 (task_id={task_id}): {cleanup_exc}")

    def _cleanup_task_media(self, task_id: Optional[str]) -> None:
        """清理本次任务产生的**中间媒体文件**（F13）。

        ## 背景

        四个转写器的 `on_finish(...)` 全被注释掉了，而 `events/handlers.py`
        里 `cleanup_temp_files` 仍然注册着 —— 也就是「事件从不触发，清理函数
        从不执行」。于是下载的音视频、Groq 压缩后的临时文件**永久堆在 data 卷**。
        报告原话：「compose `mem_limit: 4g` 时磁盘满比 CPU 先炸」。

        ## 为什么不用原来的 `cleanup_temp_files`

        它靠 `video_id` 前缀扫目录（`file.startswith(video_id)`），有两个问题：

          1. **并发不安全**：两个任务处理同一个视频时，一个结束就会删掉另一个
             还在用的文件；
          2. **会误删用户上传的原件**：`LocalDownloader.download_video()` 返回的
             是 uploads 目录里的**用户源文件**，它同样以 video_id/文件名开头。

        所以这里改成**按任务登记式**清理：只删本实例确实创建过的路径。

        ## 清理范围

        只删「可以重新生成」的东西：
          - `self.video_path`：下载/提取出的中间媒体（不是 uploads 里的原件）；
          - 转写器的临时产物（若转写器登记了）。

        **不删**：`note_results/` 里的缓存 JSON（`audio` / `transcript` /
        `markdown`）—— 那是**缓存**，重跑任务时能省一次下载与转写，删了反而
        让「重试」变慢。也不删截图/封面（属于笔记内容）。
        """
        if not task_id:
            return

        candidates: list[Path] = []

        video_path = getattr(self, "video_path", None)
        if video_path:
            candidates.append(Path(video_path))

        # 转写器可能登记了自己的临时文件（如 Groq 压缩后的 mp3）
        transcriber = getattr(self, "transcriber", None)
        for attr in ("temp_files", "last_temp_files"):
            extra = getattr(transcriber, attr, None)
            if extra:
                try:
                    candidates.extend(Path(p) for p in extra)
                except TypeError:
                    pass

        from app.utils.upload_paths import uploads_root

        try:
            uploads_dir_resolved = uploads_root().resolve()
        except Exception:  # noqa: BLE001 - 拿不到 uploads 根就保守跳过检查
            uploads_dir_resolved = None

        for path in candidates:
            try:
                resolved = path.resolve()

                # **安全护栏**：绝不动 uploads 里的用户原件
                if uploads_dir_resolved is not None:
                    try:
                        resolved.relative_to(uploads_dir_resolved)
                        logger.debug(f"跳过 uploads 内的用户文件（不清理）：{resolved}")
                        continue
                    except ValueError:
                        pass  # 不在 uploads 下 → 可以删

                if resolved.is_file():
                    resolved.unlink()
                    logger.info(f"已清理任务中间文件 (task_id={task_id}): {resolved}")
            except Exception as exc:  # noqa: BLE001 - 清理失败不该影响任务结果
                logger.warning(f"清理中间文件失败 (task_id={task_id}, {path}): {exc}")

    @staticmethod
    def delete_note(video_id: str, platform: str) -> int:
        """
        删除数据库中对应 video_id 与 platform 的任务记录

        :param video_id: 视频 ID
        :param platform: 平台标识
        :return: 删除的记录数
        """
        logger.info(f"删除笔记记录 (video_id={video_id}, platform={platform})")
        return delete_task_by_video(video_id, platform)

    def save_and_mark_success(self, task_id: str, note, save_fn) -> bool:
        """原子地「落盘结果 → 标 SUCCESS」。

        **为什么需要它**：原先 ``generate()`` 在返回前就把状态标成 SUCCESS，
        而结果文件要等 ``routers.note.run_note_task`` 拿到 NoteResult 后才写。
        这中间的窗口期内轮询会看到「成功但无结果」；一旦 ``save_fn`` 抛异常，
        状态会**永久**停在 SUCCESS 而文件不存在，轮询被 ``get_task_status``
        伪装成 PENDING，笔记永远转圈。

        顺序必须是：**先落盘，再标成功**。落盘失败则标 FAILED（不是 SUCCESS）。

        :param save_fn: 由调用方注入的落盘函数（router 侧提供，保留路径约定）
        :return: 是否成功
        """
        try:
            save_fn(task_id, note)
        except Exception as exc:
            logger.error(f"结果落盘失败，任务标失败 (task_id={task_id})：{exc}", exc_info=True)
            self._update_status(task_id, TaskStatus.FAILED,
                                message="笔记结果保存失败")
            return False

        self._update_status(task_id, TaskStatus.SUCCESS)
        logger.info(f"笔记生成成功 (task_id={task_id})")
        return True

    # ---------------- 私有方法 ----------------

    def _init_transcriber(self) -> Transcriber:
        """
        根据环境变量 TRANSCRIBER_TYPE 动态获取并实例化转写器
        """
        if self.transcriber_type not in _transcribers:
            logger.error(f"未找到支持的转写器：{self.transcriber_type}")
            raise Exception(f"不支持的转写器：{self.transcriber_type}")

        logger.info(f"使用转写器：{self.transcriber_type}")
        return get_transcriber(
            transcriber_type=self.transcriber_type,
            model_size=self.model_size,
        )

    def _get_gpt(self, model_name: Optional[str], provider_id: Optional[str]) -> GPT:
        """
        根据 provider_id 获取对应的 GPT 实例

        :param model_name: GPT 模型名称
        :param provider_id: 供应商 ID。BYOK 下形如 ``own:llm``，
            表示「用当前用户自带的凭据」而不是站点 providers 表里的记录。
        :return: GPT 实例

        ## BYOK 改动

        原来这里直接 ``ProviderService.get_provider_by_id(provider_id)`` 查全局表。
        现在统一走 ``credential_resolver.resolve()``：它是唯一的「用谁的 key」入口，
        内部按 shared/optional/required 三种模式决定，并负责用户凭据解密。

        ```own:`` 前缀由 ``model_selection._resolve_own_selection`` 校验过，
        走到这里时只剩「把能力位透传给 resolver」。
        """
        from app.services.credential_resolver import resolve as resolve_credential

        capability = "llm"
        site_provider_id = provider_id
        if provider_id and provider_id.startswith("own:"):
            # own:llm / own:default → 能力位交给 resolver 解析
            capability = provider_id.split(":", 1)[1] or "default"
            site_provider_id = None

        try:
            cred = resolve_credential(
                self.user_id,
                capability,
                model_name=model_name,
                site_provider_id=site_provider_id,
            )
        except Exception as exc:  # noqa: BLE001
            # BYOK 的 CredentialRequired / CredentialBroken 带着给用户看的文案，
            # 原样抛出即可（_friendly_error_message 会透出 detail/message）。
            logger.error(f"[get_gpt] 凭据解析失败: {exc}")
            raise ProviderError(
                code=ProviderErrorEnum.NOT_FOUND,
                message=str(exc) or ProviderErrorEnum.NOT_FOUND.message,
            ) from exc

        logger.info(
            f"创建 GPT 实例（来源={cred.source}, model={cred.model_name}, "
            f"base_url={cred.base_url}）"
        )
        config = ModelConfig(
            api_key=cred.api_key,
            base_url=cred.base_url,
            # BYOK 时模型名以凭据/调用方为准
            model_name=cred.model_name or model_name,
            provider=cred.source,
            name=cred.source,
        )
        return GPTFactory().from_config(config)

    def _get_downloader(self, platform: str) -> Downloader:
        """
        根据平台名称创建下载器实例。

        每次任务现建一个新实例（而不是复用 import 期单例），这样后台改 Cookie
        后下一个任务就能生效，不用重启后端。

        :param platform: 平台标识，需在 SUPPORT_PLATFORM_MAP 中
        :return: 对应的 Downloader 子类实例
        """
        logger.debug(f"实例化下载器 - {platform}")
        try:
            downloader = get_downloader(platform)
        except ValueError:
            logger.error(f"不支持的平台：{platform}")
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        logger.info(f"使用下载器：{type(downloader).__name__}")
        return downloader

    def _update_status(self, task_id: Optional[str], status: Union[str, TaskStatus], message: Optional[str] = None,
                       user_id: Optional[int] = None, platform: Optional[str] = None,
                       video_id: Optional[str] = None):
        """
        创建或更新 {task_id}.status.json，记录当前任务状态

        :param task_id: 任务唯一 ID
        :param status: TaskStatus 枚举或自定义状态字符串
        :param message: 可选消息，用于记录失败原因等
        :param user_id: 任务归属；**只在首次写入时落盘**，后续更新保留原值

        ## 归属为什么不能覆盖（安全）

        这个文件是 `_task_owner()` 判 ACL 的数据源。如果后来者能用 `user_id`
        覆盖已有归属，那么「拿别人的 task_id 提交一次任务」就能把别人的任务
        转到自己名下（配合 `_purge_task` / `get_task_status` 即为越权读删）。
        因此这里改为 **setdefault 语义**：文件里已有归属就绝不改写。

        全程使用 `note_file()` 拼路径，越界 task_id 会抛 `UnsafeTaskId` 而不是
        写到目录外（见 `app/utils/note_paths.py`）。
        """
        if not task_id:
            return

        NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            status_file = note_file(NOTE_OUTPUT_DIR, str(task_id), ".status.json")
        except UnsafeTaskId as exc:
            # 状态文件写不进去不该让整个任务流水线崩掉，但也必须留下痕迹：
            # 这是被利用的信号，用 error 级别记录（不是 debug）。
            logger.error(f"拒绝写入越界状态文件 (task_id={task_id!r}): {exc}")
            return
        logger.debug(f"写入状态文件: {status_file} 当前状态: {status}")

        # 先读旧内容再合并：状态会被多次覆盖写，归属信息不能丢
        data: dict = {}
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}

        data["status"] = status.value if isinstance(status, TaskStatus) else status
        if message:
            data["message"] = message
        elif self._is_retry_message(data.get("message")):
            # 阶段推进时把上一次遗留的「（重试 n/N）」清掉，否则它会一路带到
            # SUMMARIZING / SUCCESS 上，前端会显示「完成（重试 2/2）」这种鬼话。
            data.pop("message", None)
        # setdefault 语义：已有归属绝不改写（见 docstring「归属为什么不能覆盖」）
        if user_id is not None and data.get("user_id") is None:
            data["user_id"] = user_id

        # 失败时间戳：不可变语义，重试会刷新 mtime 但这里保留「这一次失败」的时刻。
        # 监控的 24h 失败统计优先用它，而不是文件 mtime（审计结论 4）。
        if str(data["status"]).upper() == str(TaskStatus.FAILED.value):
            data["failed_at"] = datetime.now(timezone.utc).isoformat()

        # 状态同步进 DB：文件会被用户删任务时物理删掉，DB 这份用来保住历史口径。
        # 失败只记日志，绝不阻断任务流程。
        try:
            update_task_status(
                task_id=task_id,
                status=data["status"],
                message=data.get("message"),
                # 必须走 self./类名：`_parse_iso` 是类里的 @staticmethod，
                # 方法体内的裸名字解析为 **global**，模块级没有这个符号 →
                # NameError → 被下面的 except 吞成 logger.debug →
                # update_task_status 一次都没执行过（DB status 恒 NULL）。
                failed_at=self._parse_iso(data.get("failed_at")),
                user_id=user_id,
                platform=platform,
                video_id=video_id,
            )
        except Exception as exc:  # noqa: BLE001 - 状态落库失败不影响任务
            # 用 warning + exc_info：这条路径静默过一次（NameError 被吞），
            # 再静默就没法在日志里发现"DB 同步整体失效"。
            logger.warning(f"同步任务状态到 DB 失败 (task_id={task_id})：{exc}", exc_info=True)

        try:
            # First create a temporary file
            temp_file = status_file.with_suffix('.tmp')

            # Write to temporary file
            with temp_file.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Atomic rename operation
            temp_file.replace(status_file)

            logger.debug(f"状态文件写入成功: {status_file}")
        except Exception as e:
            logger.error(f"写入状态文件失败 (task_id={task_id})：{e}")
            # 兜底：把错误写进旁路 .err 文件，**绝不**覆写 .status.json。
            # 旧实现直接把 "Error writing status: ..." 写进状态文件，
            # 产出一个「合法但非 JSON」的坏文件——监控页会计入 unreadable，
            # 任务本身也从此读不出状态（前端永远停在 PENDING）。
            # 保留原状态文件（哪怕旧一拍）比写坏它更安全。
            try:
                # 注意：不能用 status_file.with_suffix('.err') ——
                # "x.status.json" 的 suffix 是 ".json"，会得到 "x.status.err"。
                err_file = status_file.with_name(f"{task_id}.err")
                err_file.write_text(
                    json.dumps(
                        {
                            "error": str(e)[:500],
                            "attempted_status": data.get("status"),
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                logger.error(f"写入错误旁路文件也失败 (task_id={task_id})")

    @staticmethod
    def _parse_iso(value) -> Optional[datetime]:
        """把 ISO 时间串 / Unix 时间戳解析成带时区的 datetime，失败返回 None。"""
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str) and value.strip():
            try:
                dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return None

    @staticmethod
    def _is_retry_message(message: Optional[str]) -> bool:
        """判断一条状态消息是不是「（重试 n/N）」这种临时提示。"""
        return bool(message) and "（重试 " in str(message) and str(message).endswith("）")

    def _get_limits(self):
        """取下载上限配置；`__new__` 出来的实例（测试里常见）也能用。"""
        return getattr(self, "limits", None) or get_download_limits()

    def _retry_delay(self, attempt: int) -> int:
        """第 attempt 次重试前的等待秒数。

        attempt 从 0 开始，所以退避序列是 1s / 2s / 4s / 8s……（`2 ** attempt`）。
        抽成方法是为了让测试能覆盖成 0 秒，不必真的 sleep。
        """
        return 2 ** attempt

    def _sleep_before_retry(self, attempt: int) -> None:
        """重试前的实际等待。测试可替换此方法跳过等待。"""
        time.sleep(self._retry_delay(attempt))

    def _run_with_retry(
        self,
        task_id: Optional[str],
        step: str,
        status_phase: TaskStatus,
        func,
    ):
        """给一段流程加重试（任务级重试）。

        规则：
        - 重试次数取 `self.limits.retry_count`（默认 2，即最多尝试 3 次）
        - **只重试** `is_retryable(exc)` 为 True 的异常（网络抖动 / 临时 5xx）；
          `SizeLimitExceeded` / `DurationLimitExceeded` 这类重试无用，立刻抛
        - 退避 1s / 2s / 4s……
        - 每次重试前把状态消息更新成「<阶段>（重试 n/N）」，前端轮询能直接显示

        :param task_id: 任务 ID
        :param step: 步骤名，仅用于日志
        :param status_phase: 重试期间要落在状态文件里的阶段
        :param func: 零参可调用对象，返回值原样透传
        :return: func() 的返回值
        :raises: 不可重试异常立即抛出；重试次数耗尽的最后一次异常也抛出
        """
        retry_count = self._get_limits().retry_count
        attempt = 0
        while True:
            try:
                return func()
            except Exception as exc:
                # 不可重试的失败（超限 / 私密 / 下架）立刻交给上层
                if not is_retryable(exc):
                    logger.warning(
                        f"{step}失败且不可重试 (task_id={task_id})：{type(exc).__name__}: {exc}"
                    )
                    raise
                if attempt >= retry_count:
                    logger.error(
                        f"{step}失败，已重试 {attempt}/{retry_count} 次仍不成功 "
                        f"(task_id={task_id})：{type(exc).__name__}: {exc}"
                    )
                    raise
                delay = self._retry_delay(attempt)
                retry_no = attempt + 1
                logger.warning(
                    f"{step}失败 (task_id={task_id})：{type(exc).__name__}: {exc}；"
                    f"{delay}s 后重试 {retry_no}/{retry_count}"
                )
                self._update_status(
                    task_id,
                    status_phase,
                    message=f"{TaskStatus.description(status_phase)}（重试 {retry_no}/{retry_count}）",
                )
                self._sleep_before_retry(attempt)
                attempt += 1

    def _load_transcript_cache(
        self,
        cache: NoteCachePaths,
        task_id: Optional[str],
    ) -> Optional[TranscriptResult]:
        """读取转写缓存：先读带指纹的新 key，再回退到老 key。

        写只在 `_fetch_source` 里写新 key；老 key 仅用于兼容历史任务缓存。
        """
        cache_file = _first_existing(cache.transcript, cache.legacy_transcript)
        if cache_file is None:
            return None

        if cache_file == cache.legacy_transcript:
            logger.info(f"命中历史命名的转写缓存 ({cache_file})，按向后兼容规则读取")
        else:
            logger.info(f"检测到转写缓存 ({cache_file})，尝试读取")
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
            transcript = TranscriptResult(
                language=data.get("language"),
                full_text=data["full_text"],
                segments=segments,
            )
            logger.info(f"已从缓存加载转写结果，共 {len(segments)} 段")
            return transcript
        except Exception as e:
            logger.warning(f"加载转写缓存失败，将重新获取：{e} (task_id={task_id})")
            return None

    def _fetch_source(
        self,
        downloader: Downloader,
        video_url: Union[str, HttpUrl],
        quality: DownloadQuality,
        platform: str,
        task_id: Optional[str],
        cache: NoteCachePaths,
        output_path: Optional[str],
        screenshot: bool,
        video_understanding: bool,
        video_interval: int,
        grid_size: List[int],
        holder: dict,
    ) -> Tuple[Optional[TranscriptResult], AudioDownloadResult]:
        """下载 + 取字幕的完整一步，作为 `_run_with_retry` 的重试单元。

        `holder` 在多次尝试之间共享：已经拿到的平台字幕、以及「已确认无字幕」
        这个结论都会记住，重试时不必再把同一个请求打一遍。

        :return: (transcript, audio_meta)
        """
        transcript = holder.get("transcript")

        # 1. 平台字幕（优先于音频下载）。字幕获取失败不算致命，退回音频转写。
        #    subtitles_checked 用来避免重试时重复请求：无字幕是稳定结论，
        #    重试下载时没必要再打一次字幕接口。
        if transcript is None and not holder.get("subtitles_checked"):
            logger.info("尝试获取平台字幕（优先于音频下载）...")
            try:
                transcript = downloader.download_subtitles(video_url)
                if transcript and transcript.segments:
                    logger.info(f"成功获取平台字幕，共 {len(transcript.segments)} 段")
                    cache.transcript.write_text(
                        json.dumps(asdict(transcript), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    holder["transcript"] = transcript
                else:
                    transcript = None
                    logger.info("平台无可用字幕，将下载音频后转写")
            except Exception as e:
                logger.warning(f"获取平台字幕失败: {e}，将下载音频后转写")
                transcript = None
            holder["subtitles_checked"] = True

        # 2. 下载音频/视频。
        # 有字幕时只提取元信息，不下载音视频文件（除非需要截图/视频理解）。
        need_full_download = transcript is None or screenshot or video_understanding
        audio_meta = self._download_media(
            downloader=downloader,
            video_url=video_url,
            quality=quality,
            task_id=task_id,
            cache=cache,
            status_phase=TaskStatus.DOWNLOADING,
            platform=platform,
            output_path=output_path,
            screenshot=screenshot,
            video_understanding=video_understanding,
            video_interval=video_interval,
            grid_size=grid_size,
            skip_download=not need_full_download,
        )

        # 3. 没有平台字幕时走音频转写
        if transcript is None:
            transcript = self._get_transcript(
                downloader=downloader,
                video_url=video_url,
                audio_file=audio_meta.file_path,
                transcript_cache_file=cache.transcript,
                status_phase=TaskStatus.TRANSCRIBING,
                task_id=task_id,
                # 上面第 1 步已经试过字幕了，这里别再打一遍网络请求
                attempt_subtitles=False,
            )
            holder["transcript"] = transcript

        return transcript, audio_meta

    def _handle_exception(self, task_id, exc):
        logger.error(f"任务异常 (task_id={task_id})", exc_info=True)
        error_message = self._friendly_error_message(exc)
        self._update_status(task_id, TaskStatus.FAILED, message=error_message)

    def _friendly_error_message(self, exc: BaseException) -> str:
        """把异常翻译成用户能看懂的中文失败原因。

        `SizeLimitExceeded` / `DurationLimitExceeded` 是配置层面的拒绝，
        直接把异常的英文串丢给用户毫无帮助，这里补上当前上限与调整方式。
        """
        limits = self._get_limits()
        if isinstance(exc, SizeLimitExceeded):
            return (
                f"视频超过 {limits.max_size_mb}MB 大小限制，已中止下载"
                f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整上限）"
            )
        if isinstance(exc, DurationLimitExceeded):
            return (
                f"视频时长超过 {limits.max_duration_seconds} 秒限制，已中止下载"
                f"（可用环境变量 DOWNLOAD_MAX_DURATION_SECONDS 调整上限）"
            )

        error_message = getattr(exc, "detail", str(exc))
        if isinstance(error_message, dict):
            try:
                error_message = json.dumps(error_message, ensure_ascii=False)
            except:
                error_message = str(error_message)
        return error_message

    def _download_media(
        self,
        downloader: Downloader,
        video_url: Union[str, HttpUrl],
        quality: DownloadQuality,
        task_id: Optional[str],
        cache: NoteCachePaths,
        status_phase: TaskStatus,
        platform: str,
        output_path: Optional[str],
        screenshot: bool,
        video_understanding: bool,
        video_interval: int,
        grid_size: List[int],
        skip_download: bool = False,
    ) -> AudioDownloadResult | None:
        """
        1. 检查音频缓存；若不存在，则根据需要下载音频或视频（若需截图/可视化）。
        2. 如果需要视频，则先下载视频并生成缩略图集，再下载音频。
        3. 返回 AudioDownloadResult

        :param downloader: Downloader 实例
        :param video_url: 视频/音频链接
        :param quality: 音频下载质量
        :param task_id: 任务 ID（缓存文件名里的一段，不能从文件名反推）
        :param cache: 本任务的缓存路径集合
        :param status_phase: 对应的状态枚举，如 TaskStatus.DOWNLOADING
        :param platform: 平台标识
        :param output_path: 下载输出目录（可为 None）
        :param screenshot: 是否需要在笔记中插入截图
        :param video_understanding: 是否需要生成缩略图
        :param video_interval: 视频截帧间隔
        :param grid_size: 缩略图网格尺寸
        :return: AudioDownloadResult 对象
        """
        audio_cache_file = cache.audio
        self._update_status(task_id, status_phase)

        # 已有缓存，尝试加载。新 key 优先，没有再看一次老 key（历史任务缓存）。
        cache_file = _first_existing(cache.audio, cache.legacy_audio)
        if cache_file is not None:
            if cache_file == cache.legacy_audio:
                logger.info(f"命中历史命名的音频缓存 ({cache_file})，按向后兼容规则读取")
            else:
                logger.info(f"检测到音频缓存 ({cache_file})，直接读取")
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return AudioDownloadResult(**data)
            except Exception as e:
                logger.warning(f"读取音频缓存失败，将重新下载：{e}")

        # 有字幕且不需要截图/视频理解时，只提取元信息不下载文件
        if skip_download:
            logger.info("已有字幕，仅提取视频元信息（不下载音视频）")
            try:
                audio = downloader.download(
                    video_url=video_url,
                    quality=quality,
                    output_dir=output_path,
                    need_video=False,
                    skip_download=True,
                )
                audio_cache_file.write_text(
                    json.dumps(asdict(audio), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"元信息提取完成 ({audio_cache_file})")
                return audio
            except Exception as exc:
                # 上限类拒绝要立刻上抛：`skip_download` 只取元信息都超限，说明这个
                # 视频本身就不该下，回退去完整下载只会白费流量再撞一次同一个上限。
                if isinstance(exc, (SizeLimitExceeded, DurationLimitExceeded)):
                    logger.info(
                        f"元信息提取时已触发上限（{type(exc).__name__}: {exc}），"
                        f"不再回退完整下载"
                    )
                    raise
                # 这不是错误，是设计好的回退：某些平台/版本拿不到纯元信息时
                # 仍要走完整下载，否则整个任务会因为省流量优化失败而挂掉。
                # 用 info 而非 warning，避免每次正常运行都刷一条看起来像故障的日志。
                logger.info(
                    f"元信息提取未成功（{type(exc).__name__}: {exc}），"
                    f"回退为完整下载（会重新下载音视频）"
                )
                logger.info("回退已生效：本次任务将走完整下载而不是仅取元信息")

        # 判断是否需要下载视频
        need_video = screenshot or video_understanding
        if screenshot and not grid_size:
            grid_size = [2, 2]

        frame_interval = video_interval if video_interval and video_interval > 0 else 6
        if need_video:
            try:
                logger.info("开始下载视频")
                video_path_str = downloader.download_video(video_url)
                self.video_path = Path(video_path_str)
                # 同样不能信「调用成功」：yt-dlp 的 max_filesize / match_filter
                # 跳过时返回码仍是 0 且不产生文件，这里必须自己验证视频真的落盘。
                self._assert_path_ready(
                    self.video_path,
                    label="视频文件",
                    video_url=video_url,
                    is_video=True,
                )
                logger.info(f"视频下载完成：{self.video_path}")

                if grid_size:
                    self.video_img_urls = VideoReader(
                        video_path=str(self.video_path),
                        grid_size=tuple(grid_size),
                        frame_interval=frame_interval,
                        unit_width=960,
                        unit_height=540,
                        save_quality=80,
                    ).run()
                else:
                    logger.info("未指定 grid_size，跳过缩略图生成")
            except Exception as exc:
                # 不在这里落 FAILED：外层 _run_with_retry 可能还要重试，
                # 中途写 FAILED 会让前端提前放弃任务。
                logger.error(f"视频下载失败：{exc}")
                raise

        # 下载音频
        try:
            logger.info("开始下载音频")
            audio = downloader.download(
                video_url=video_url,
                quality=quality,
                output_dir=output_path,
                need_video=need_video,
            )
            self._assert_media_file_ready(audio, video_url=video_url)
            audio_cache_file.write_text(json.dumps(asdict(audio), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"音频下载并缓存成功 ({audio_cache_file})")
            return audio
        except Exception as exc:
            # 同上：交给外层重试决定是否真的失败
            logger.error(f"音频下载失败：{exc}")
            raise

    def _assert_media_file_ready(self, audio: AudioDownloadResult, video_url: Union[str, HttpUrl]) -> None:
        """校验下载器返回的媒体文件确实落盘且非空。

        为什么必须有这一层：yt-dlp 的 `max_filesize` 只在拿到 Content-Length 后、
        写第一个字节前检查一次，超限时**直接返回 False——不产生文件、不抛异常、
        退出码仍是 0**。`--match-filter` 跳过时同理。也就是说「下载器调用成功返回」
        完全不等于「文件存在」，只能由调用方拿返回值去验证。

        命中时抛 `RetryableDownloadError`（残文件通常是网络中断留下的，重下一次
        大多能成功）。唯一的例外：如果返回值里**明确带了**超过上限的 filesize /
        duration，说明大概率是被上限拦掉的，那就抛对应的不可重试异常，避免白重试。

        :param audio: 下载器返回的结果
        :param video_url: 原始链接，仅用于错误信息
        :raises RetryableDownloadError / SizeLimitExceeded / DurationLimitExceeded
        """
        file_path = getattr(audio, "file_path", None)
        path = Path(file_path) if file_path else None

        if path is not None and path.exists():
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise RetryableDownloadError(f"无法读取音频文件大小：{path}（{exc}）") from exc
            if size > 0:
                return

        # 到这里说明文件不存在或为 0 字节。先判断是不是「被上限静默跳过」的情况。
        self._raise_if_skipped_by_limit(audio)

        if path is None:
            raise RetryableDownloadError(
                f"下载器未返回音频文件路径（{video_url}），可能是下载中断"
            )
        if not path.exists():
            raise RetryableDownloadError(
                f"音频文件未生成：{path}（下载可能被中途跳过或中断，可重试）"
            )
        raise RetryableDownloadError(
            f"音频文件为空：{path}（下载可能被截断，可重试）"
        )

    @staticmethod
    def _assert_path_ready(
        path: Path,
        *,
        label: str,
        video_url: Union[str, HttpUrl],
        is_video: bool = False,
    ) -> None:
        """校验下载器返回的路径确实存在且非空（视频/音频通用）。

        :raises RetryableDownloadError: 文件缺失或为空。视频路径缺失时不额外区分
            超限（超限信息在 audio 结果里，视频这一步拿不到）。
        """
        if not path.exists():
            raise RetryableDownloadError(
                f"{label}未生成：{path}（下载可能被中途跳过或中断，可重试；source={video_url}）"
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RetryableDownloadError(f"无法读取{label}大小：{path}（{exc}）") from exc
        if size <= 0:
            raise RetryableDownloadError(
                f"{label}为空：{path}（下载可能被截断，可重试）"
            )

    def _raise_if_skipped_by_limit(self, audio: AudioDownloadResult) -> None:
        """文件缺失时，尽量把「被上限静默跳过」和「网络中断」区分开。

        只看返回值里**确实存在**的元信息，不做猜测：
        - `raw_info` 里的 `filesize` / `filesize_approx` 超过体积上限
        - `duration` 超过时长上限
        命中就抛不可重试的对应异常。信息不足时什么都不做，让调用方按可重试处理。
        """
        limits = self._get_limits()

        raw_info = getattr(audio, "raw_info", None) or {}
        if isinstance(raw_info, dict):
            for key in ("filesize", "filesize_approx"):
                declared = raw_info.get(key)
                if isinstance(declared, (int, float)) and limits.size_exceeds(int(declared)):
                    raise SizeLimitExceeded(
                        f"媒体体积 {int(declared)} 字节超过上限 {limits.max_size_mb}MB，"
                        f"已被下载器跳过"
                    )

        duration = getattr(audio, "duration", None)
        if isinstance(duration, (int, float)) and limits.duration_exceeds(duration):
            raise DurationLimitExceeded(
                f"视频时长 {duration} 秒超过上限 {limits.max_duration_seconds} 秒，"
                f"已被下载器跳过"
            )


    def _get_transcript(
        self,
        downloader: Downloader,
        video_url: str,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        task_id: Optional[str] = None,
        attempt_subtitles: bool = True,
    ) -> TranscriptResult | None:
        """
        优先获取平台字幕，没有则 fallback 到音频转写

        :param downloader: 下载器实例
        :param video_url: 视频链接
        :param audio_file: 音频文件路径（用于 fallback 转写）
        :param transcript_cache_file: 缓存文件路径
        :param status_phase: 状态枚举
        :param task_id: 任务 ID
        :param attempt_subtitles: 是否再尝试一次平台字幕。调用方（_fetch_source）
            已经试过字幕时传 False，避免同一个请求打两遍
        :return: TranscriptResult 对象
        """
        self._update_status(task_id, status_phase)

        # 已有缓存，直接返回
        if transcript_cache_file.exists():
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                return TranscriptResult(language=data.get("language"), full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新获取：{e}")

        # 1. 先尝试获取平台字幕
        if attempt_subtitles:
            logger.info("尝试获取平台字幕...")
            try:
                transcript = downloader.download_subtitles(video_url)
                if transcript and transcript.segments:
                    logger.info(f"成功获取平台字幕，共 {len(transcript.segments)} 段")
                    # 缓存结果
                    transcript_cache_file.write_text(
                        json.dumps(asdict(transcript), ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                    return transcript
                else:
                    logger.info("平台无可用字幕，将使用音频转写")
            except Exception as e:
                logger.warning(f"获取平台字幕失败: {e}，将使用音频转写")
        else:
            logger.info("平台字幕已在下载阶段尝试过且无可用结果，直接走音频转写")

        # 2. Fallback 到音频转写
        return self._transcribe_audio(
            audio_file=audio_file,
            transcript_cache_file=transcript_cache_file,
            status_phase=status_phase,
            task_id=task_id,
        )

    def _transcribe_audio(
        self,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        task_id: Optional[str] = None,
    ) -> TranscriptResult | None:
        """
        1. 检查转写缓存；若存在则尝试加载，否则调用转写器生成并缓存。
        2. 返回 TranscriptResult 对象

        :param audio_file: 音频文件本地路径
        :param transcript_cache_file: 转写结果缓存路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.TRANSCRIBING
        :param task_id: 任务 ID（缓存文件名带指纹，不能再从文件名反推）
        :return: TranscriptResult 对象
        """
        self._update_status(task_id, status_phase)

        # 已有缓存，尝试加载
        if transcript_cache_file.exists():
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                return TranscriptResult(language=data["language"], full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新转写：{e}")

        # 调用转写器
        try:
            logger.info("开始转写音频")
            transcript = self.transcriber.transcript(file_path=audio_file)
            transcript_cache_file.write_text(json.dumps(asdict(transcript), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"转写并缓存成功 ({transcript_cache_file})")
            return transcript
        except Exception as exc:
            # 这里**不能**落 FAILED：本方法在 _run_with_retry 的重试范围内被调用，
            # 中途写 FAILED 会让前端轮询到失败后停止等待，后面的重试成功也白搭。
            # 失败状态统一由 generate() 的 except 分支写一次。
            logger.error(f"音频转写失败：{exc}")
            raise

    def _summarize_text(
        self,
        audio_meta: AudioDownloadResult,
        transcript: TranscriptResult,
        gpt: GPT,
        markdown_cache_file: Path,
        task_id: Optional[str] = None,
        link: bool = False,
        screenshot: bool = False,
        formats: Optional[List[str]] = None,
        style: Optional[str] = None,
        extras: Optional[str] = None,
            video_img_urls: Optional[List[str]] = None,
    ) -> str | None:
        """
        调用 GPT 对转写结果进行总结，生成 Markdown 文本并缓存。

        :param audio_meta: AudioDownloadResult 元信息
        :param transcript: TranscriptResult 转写结果
        :param gpt: GPT 实例
        :param markdown_cache_file: Markdown 缓存路径
        :param task_id: 任务 ID（缓存文件名带指纹，不能再从文件名反推）
        :param link: 是否在笔记中插入链接
        :param screenshot: 是否在笔记中生成截图占位
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param style: GPT 输出风格
        :param extras: GPT 额外参数
        :return: 生成的 Markdown 字符串
        """
        self._update_status(task_id, TaskStatus.SUMMARIZING)

        source = GPTSource(
            title=audio_meta.title,
            segment=transcript.segments,
            tags=audio_meta.raw_info.get("tags", []),
            screenshot=screenshot,
            video_img_urls=video_img_urls or [],
            link=link,
            _format=formats or [],
            style=style,
            extras=extras,
            checkpoint_key=task_id,
        )

        try:
            markdown = gpt.summarize(source)
            markdown_cache_file.write_text(markdown, encoding="utf-8")
            logger.info(f"GPT 总结并缓存成功 ({markdown_cache_file})")
            return markdown
        except Exception as exc:
            # 同 _transcribe_audio：失败状态统一由 generate() 写，避免重复/提前落 FAILED
            logger.error(f"GPT 总结失败：{exc}")
            raise

    def _post_process_markdown(
        self,
        markdown: str,
        video_path: Optional[Path],
        formats: List[str],
        audio_meta: AudioDownloadResult,
        platform: str,
        task_id: Optional[str] = None,
    ) -> str:
        """
        对生成的 Markdown 做后期处理：插入截图和/或插入链接。

        :param markdown: 原始 Markdown 字符串
        :param video_path: 本地视频路径（可为 None）
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param audio_meta: AudioDownloadResult 元信息，用于链接替换
        :param platform: 平台标识，用于链接替换
        :return: 处理后的 Markdown 字符串
        """
        if "screenshot" in formats and video_path:
            try:
                replaced = self._insert_screenshots(markdown, video_path, task_id=task_id)
                # 防御：任何情况下都不能让后处理把一个正常字符串变成 None/空，
                # 否则整篇笔记会变空白而任务仍被标 SUCCESS。
                if isinstance(replaced, str) and replaced.strip():
                    markdown = replaced
                else:
                    logger.warning("截图插入返回了非字符串结果，保留原始 markdown")
            except Exception as exc:
                logger.warning(f"截图插入失败，跳过该步骤：{exc}")

        if "link" in formats:
            try:
                replaced = replace_content_markers(markdown, video_id=audio_meta.video_id, platform=platform)
                if isinstance(replaced, str) and replaced.strip():
                    markdown = replaced
                else:
                    logger.warning("链接插入返回了非字符串结果，保留原始 markdown")
            except Exception as e:
                logger.warning(f"链接插入失败，跳过该步骤：{e}")

        return markdown

    def _insert_screenshots(self, markdown: str, video_path: Path, task_id: Optional[str] = None) -> str:
        """
        扫描 Markdown 文本中所有 Screenshot 标记，并替换为实际生成的截图链接。

        **用户隔离**：截图落到 `screenshots/<task_id>/` 子目录，而不是全部平铺在
        根目录。理由：旧实现用全局目录 + UUID 文件名，虽然不会撞名，但
        任何拿到文件名的人都能访问别人的截图，也无法按用户/任务清理。
        用 task_id 分目录后，任务归属（状态文件里的 user_id）天然决定了
        目录归属，删除任务时也能整目录回收。

        **失败语义**：单张截图失败只跳过该标记（连同标记本身一并去掉，避免
        用户看到残留的 `*Screenshot-00:05` 字样），其余内容照常返回。

        :param markdown: 含有 *Screenshot-mm:ss 或 Screenshot-[mm:ss] 标记的 Markdown 文本
        :param video_path: 本地视频文件路径
        :param task_id: 任务 ID，用作子目录名；为空时退回根目录（兼容旧调用）
        :return: 替换后的 Markdown 字符串（永不返回 None）
        """
        # 隔离目录：screenshots/<task_id>/
        if task_id:
            safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(task_id))
            out_dir = Path(IMAGE_OUTPUT_DIR) / safe
            url_prefix = f"{IMAGE_BASE_URL.rstrip('/')}/{safe}"
        else:
            out_dir = Path(IMAGE_OUTPUT_DIR)
            url_prefix = IMAGE_BASE_URL.rstrip('/')

        matches: List[Tuple[str, int]] = extract_screenshot_timestamps(markdown)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(str(video_path), str(out_dir), ts, idx)
                filename = Path(img_path).name
                # 构建前端可访问 URL，例如 /static/screenshots/<task_id>/xxx.jpg
                img_url = f"{url_prefix}/{filename}"
                # F9：`<img>` 带不上 Cookie/API-Key（Tauri / 浏览器扩展），
                # 所以令牌必须在 URL **写进 markdown 的那一刻**就带上——
                # 之后整段 markdown 会被持久化，再没有补签的机会。
                img_url = _sign_static_url(img_url)
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
            except Exception as exc:
                logger.error(f"生成截图失败 (timestamp={ts})：{exc}，跳过该截图")
                # 去掉残留标记，让笔记保持可读；其余截图继续处理
                markdown = markdown.replace(marker, "", 1)
        return markdown

    @staticmethod
    def _extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
        """
        从 Markdown 文本中提取所有 '*Screenshot-mm:ss' 或 'Screenshot-[mm:ss]' 标记，
        返回 [(原始标记文本, 时间戳秒数), ...] 列表。

        :param markdown: 原始 Markdown 文本
        :return: 标记与对应时间戳秒数的列表
        """
        return extract_screenshot_timestamps(markdown)

    def _save_metadata(self, video_id: str, platform: str, task_id: str) -> None:
        """
        将生成的笔记任务记录插入数据库

        :param video_id: 视频 ID
        :param platform: 平台标识
        :param task_id: 任务 ID
        """
        try:
            insert_video_task(
                video_id=video_id,
                platform=platform,
                task_id=task_id,
                user_id=self._read_task_owner(task_id),
            )
            logger.info(f"已保存任务记录到数据库 (video_id={video_id}, platform={platform}, task_id={task_id})")
        except Exception as e:
            logger.error(f"保存任务记录失败：{e}")

    @staticmethod
    def _read_task_owner(task_id: str) -> Optional[int]:
        """从状态文件里读出任务归属（路由在首次写 PENDING 时落进去的）。

        之前入库时没带 user_id，导致 video_tasks 的归属一直是 NULL，
        按用户隔离的历史/删除逻辑只能靠文件名兜底。
        """
        try:
            status_file = NOTE_OUTPUT_DIR / f"{task_id}.status.json"
            if not status_file.exists():
                return None
            payload = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                owner = payload.get("user_id")
                return int(owner) if owner is not None else None
        except Exception:
            return None
        return None
