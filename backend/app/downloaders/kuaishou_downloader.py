"""快手下载器。

本次修复：

1. **requests 没有 timeout**：下载 mp4 时 `requests.get(..., stream=True)` 没带
   timeout，CDN 一挂就挂死任务线程。统一 `timeout=(5, 30)`，并按块写入时检查
   socket 异常。
2. **体积上限**：`self.limits.max_size_bytes`，流式写入过程中边写边算，
   超限立刻中断并删除半成品（不把 500MB 写到磁盘再判断）。
3. **`skip_download` 参数**：note.py 在「已经有字幕、不需要音视频」时会传
   `skip_download=True`。历史实现签名里没有这个参数，直接 TypeError 被
   `except Exception` 吞掉后回退**完整下载**，省流量优化彻底失效。
4. **`download_video` 返回值不可信**：原来 `return self.download(...).video_path`。
   如果 `download()` 命中「已有 mp3 缓存」提前返回，`video_path` 指向的 mp4
   可能根本不存在（用户删过、或上一次只下了音频）。现在返回前校验存在且非空。
5. **print 改 logger**：`print(video_raw_info)` 会把整个详情打到 stdout。
6. **`photo_info['caption']` 可能是 None**：`.strip()` 会 AttributeError。
7. **时长单位**：快手 `photo.duration` 是毫秒（见 kuaishou_helper 的说明），
   归一成秒再返回，否则 11 秒的视频会被时长上限判成 11000 秒直接拒掉。

Cookie / 详情获取全在 `kuaishou_helper/kuaishou.py`，那边已改成每次现读。
"""

import os
import subprocess
from abc import ABC
from typing import Optional, Union

import requests

from app.downloaders.base import Downloader, audio_bitrate_kbps
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)
from app.downloaders.kuaishou_helper.kuaishou import (
    KuaiShou,
    KuaishouError,
    normalize_duration_seconds,
)
from app.enmus.note_enums import DownloadQuality
from app.utils.logger import get_logger
from app.models.audio_model import AudioDownloadResult
from app.utils.path_helper import get_data_dir

logger = get_logger(__name__)

# 直连 CDN 下载视频，连接 5s / 读 30s。没有 timeout 时 requests 会无限等。
DOWNLOAD_TIMEOUT = (5, 30)
CHUNK_SIZE = 1024 * 1024


class KuaiShouDownloader(Downloader, ABC):
    def __init__(self, user_id: Optional[int] = None):
        super().__init__(user_id=user_id)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _resolve_dir(self, output_dir: Optional[str]) -> str:
        resolver = getattr(self, "_resolve_output_dir", None)
        if callable(resolver):
            return resolver(output_dir)
        resolved = output_dir or get_data_dir() or getattr(self, "cache_data", None)
        if not resolved:
            raise RuntimeError("无法确定下载输出目录")
        os.makedirs(resolved, exist_ok=True)
        return resolved

    @staticmethod
    def _clean_title(caption) -> str:
        """把 caption 整成可读标题。caption 可能是 None（历史代码会 AttributeError）。"""
        return (caption or "").replace("\n", "").strip()

    def _fetch_raw_info(self, video_url: str) -> dict:
        """取作品详情（GraphQL 的 data 段）。

        `KuaishouError` 是「解析/风控失败」这类不可重试错误，转成
        `RetryableDownloadError` 会触发任务级重试三次白跑，所以这里原样上抛，
        由 note.py 的 `is_retryable()` 判成不可重试（DownloadError 基类）。

        非 staticmethod：BYOK 需要 ``self.user_id`` 才能选对 Cookie。
        调用点本来就是 ``self._fetch_raw_info(...)``，签名变化对调用方透明。
        """
        return KuaiShou(user_id=self.user_id).run(video_url)

    def _limits_now(self):
        return getattr(self, "limits", None)

    def _stream_download(self, url: str, target_path: str) -> int:
        """流式下载到目标文件，返回实际字节数。

        边下边算体积：超过 `max_size_bytes` 立即停止并删除半成品，
        而不是先写满磁盘再由调用方判断。
        """
        limits = self._limits_now()
        max_size = limits.max_size_bytes if limits is not None else None

        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.status_code != 200:
                raise RetryableDownloadError(
                    f"快手视频下载失败：HTTP {response.status_code}"
                )

            written = 0
            with open(target_path, "wb") as handle:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_size is not None and written > max_size:
                        handle.close()
                        self._remove_quietly(target_path)
                        raise SizeLimitExceeded(
                            f"快手视频超过 {limits.max_size_mb}MB 大小上限，已中止下载"
                            f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
                        )
                    handle.write(chunk)

        if written <= 0:
            self._remove_quietly(target_path)
            raise RetryableDownloadError("快手视频下载得到空文件，稍后重试")
        return written

    @staticmethod
    def _remove_quietly(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _convert_to_mp3(mp4_path: str, mp3_path: str, quality) -> None:
        """用 ffmpeg 抽音轨并编码 mp3，码率跟随 quality（fast=64/medium=128/slow=192）。"""
        bitrate = audio_bitrate_kbps(quality)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", mp4_path,
                    "-vn",
                    "-ac", "1",              # 单声道：whisper 只吃单声道
                    "-acodec", "libmp3lame",
                    "-b:a", f"{bitrate}k",
                    mp3_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,                 # 没有 timeout 时 ffmpeg 卡住会拖死任务
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未安装 ffmpeg，无法把快手视频转成音频") from exc
        except subprocess.TimeoutExpired as exc:
            raise RetryableDownloadError("ffmpeg 转码超时，稍后重试") from exc
        except subprocess.CalledProcessError as exc:
            raise RetryableDownloadError("ffmpeg 转换 MP3 失败") from exc

    @staticmethod
    def _build_result(
        *,
        file_path: str,
        title: str,
        duration: float,
        cover_url,
        video_id: str,
        tags: str,
        video_path: Optional[str],
    ) -> AudioDownloadResult:
        return AudioDownloadResult(
            file_path=file_path,
            title=title,
            duration=duration,
            cover_url=cover_url,
            platform="kuaishou",
            video_id=video_id,
            raw_info={"tags": tags},
            video_path=video_path,
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        output_dir = self._resolve_dir(output_dir)

        # 1) 取详情。skip_download 也走这里（本来就只需要元信息）。
        data = self._fetch_raw_info(video_url)
        detail = data.get("visionVideoDetail") or {}
        photo_info = detail.get("photo") or {}
        if not photo_info:
            raise KuaishouError("快手视频详情缺少 photo 字段")
        video_id = photo_info.get("id")
        if not video_id:
            raise KuaishouError("快手视频详情缺少 photo.id")

        title = self._clean_title(photo_info.get("caption"))
        duration = normalize_duration_seconds(photo_info.get("duration"))
        cover_url = photo_info.get("coverUrl")
        tags = ",".join(
            tag.get("name") for tag in (data.get("tags") or []) if tag.get("name")
        )

        # 2) 时长上限：拿到 duration 就判，超限不必下载。
        limits = self._limits_now()
        if limits is not None and limits.duration_exceeds(duration):
            raise DurationLimitExceeded(
                f"快手视频时长 {duration:.0f} 秒，超过 "
                f"{limits.max_duration_seconds} 秒上限"
                f"（可用环境变量 DOWNLOAD_MAX_DURATION_SECONDS 调整）"
            )

        mp4_path = os.path.join(output_dir, f"{video_id}.mp4")
        mp3_path = os.path.join(output_dir, f"{video_id}.mp3")

        if skip_download:
            # 只取元信息：不下载任何媒体，file_path 按约定返回（不保证存在）。
            logger.info("快手仅提取元信息（未下载音视频）")
            return self._build_result(
                file_path=mp3_path,
                title=title,
                duration=duration,
                cover_url=cover_url,
                video_id=video_id,
                tags=tags,
                video_path=mp4_path if os.path.isfile(mp4_path) else None,
            )

        # 3) 已有 mp3 缓存：直接复用。
        if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
            logger.info(f"快手音轨已存在，跳过下载: {mp3_path}")
            return self._build_result(
                file_path=mp3_path,
                title=title,
                duration=duration,
                cover_url=cover_url,
                video_id=video_id,
                tags=tags,
                video_path=mp4_path if os.path.isfile(mp4_path) else None,
            )

        # 4) 下载 mp4（如果已有非空 mp4 就复用，避免重复下）。
        if not (os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 0):
            photo_url = photo_info.get("photoUrl")
            if not photo_url:
                raise KuaishouError("快手视频详情缺少 photoUrl")
            logger.info(f"快手开始下载视频: {video_id}")
            size = self._stream_download(photo_url, mp4_path)
            logger.info(f"快手视频下载完成: {mp4_path}（{size / 1024 / 1024:.1f}MB）")
        else:
            logger.info(f"快手视频已存在，跳过下载: {mp4_path}")

        # 5) 抽音轨转 mp3（码率跟随 quality）。
        self._convert_to_mp3(mp4_path, mp3_path, quality)
        if not (os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0):
            raise RetryableDownloadError(f"快手音轨生成失败：{mp3_path}")

        return self._build_result(
            file_path=mp3_path,
            title=title,
            duration=duration,
            cover_url=cover_url,
            video_id=video_id,
            tags=tags,
            video_path=mp4_path,
        )

    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        """下载并返回视频文件路径。

        历史实现直接 `return self.download(...).video_path`：如果 `download()`
        命中「已有 mp3」提前返回，video_path 指向的 mp4 可能不存在（例如上次
        只成功转了音频、或用户手动删过），下游 VideoReader 会拿到不存在的路径。
        这里返回前必须校验存在且非空。
        """
        output_dir = self._resolve_dir(output_dir)
        result = self.download(video_url, output_dir)
        video_path = result.video_path

        if not video_path:
            raise RetryableDownloadError("快手视频下载失败：未返回视频文件路径")
        if not os.path.isfile(video_path) or os.path.getsize(video_path) <= 0:
            raise RetryableDownloadError(
                f"快手视频文件未找到或为空：{video_path}（下载可能被中断，可重试）"
            )
        return video_path


if __name__ == "__main__":
    ks = KuaiShouDownloader()
    ks.download(
        "https://v.kuaishou.com/2vBqX74 王宝强携手刘昊然、岳云鹏上演精彩名场面 全程高能 看一遍笑一遍"
    )
