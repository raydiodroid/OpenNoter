"""
通过 youtube_transcript_api 获取 YouTube 字幕。
优先人工字幕，其次自动生成字幕。不依赖 yt_dlp，无需下载任何文件。

本次修复（都是实测能复现的）：

1. **`FetchedTranscriptSnippet` 不是 dict**：`youtube-transcript-api` 1.x 把它改成了
   dataclass（`text` / `start` / `duration` 三个属性）。旧代码写的是
   `snippet.get("text", "")`，在 dataclass 上会 `AttributeError` 被外层
   `except Exception` 吞掉 —— 但**更糟的是它没走到那一步**：
   `isinstance(snippet, dict)` 为 False 时会退化成 `str(snippet)`，于是每段字幕都
   变成 `"FetchedTranscriptSnippet(text='...', start=1.36, duration=1.68)"` 这样的
   字符串，且 `start` 恒为 0。实测 61 段的字幕全被写成同一时间点、正文里塞满
   dataclass 的 repr —— LLM 摘要直接喂垃圾。现在优先按属性取值，dict 兼容保留。
2. **零时长 / 空文本段**：先过滤再算 `end`，避免 `end == start` 的段把时间轴压平。
3. 所有网络异常都吞成 `None`（保持历史契约：无字幕 ≠ 任务失败），但日志分级：
   有字幕轨道却取失败 → warning；确实没字幕 → info。

代理注入逻辑保留：国内直连 youtube 会超时，配了全局代理就套上。
"""

from typing import List, Optional

from youtube_transcript_api import YouTubeTranscriptApi

from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.proxy_config_manager import ProxyConfigManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _snippet_value(snippet, key: str, default=None):
    """从字幕片段里取值，兼容 dict 与 dataclass 两种形态。

    youtube-transcript-api 1.x 返回 `FetchedTranscriptSnippet`（dataclass），
    历史上也返回过 dict。两种都要支持，否则字幕会静默变空。
    """
    if isinstance(snippet, dict):
        return snippet.get(key, default)
    return getattr(snippet, key, default)


class YouTubeSubtitleFetcher:
    """通过 youtube-transcript-api 获取 YouTube 字幕。"""

    def __init__(self):
        # 配了全局代理就给 youtube-transcript-api 套一个带 proxies 的 requests.Session，
        # 否则国内拉字幕同样会超时。代理未配置时退回默认无代理客户端。
        proxy = ProxyConfigManager().get_proxy_url()
        if proxy:
            try:
                import requests
                session = requests.Session()
                session.proxies = {"http": proxy, "https": proxy}
                self._api = YouTubeTranscriptApi(http_client=session)
                logger.info(f"YouTube 字幕走代理: {proxy}")
            except Exception as e:
                logger.warning(f"为 youtube-transcript-api 注入代理失败，回退无代理: {e}")
                self._api = YouTubeTranscriptApi()
        else:
            self._api = YouTubeTranscriptApi()

    def fetch_subtitles(
        self,
        video_id: str,
        langs: Optional[List[str]] = None,
    ) -> Optional[TranscriptResult]:
        if langs is None:
            langs = ["zh-Hans", "zh", "zh-CN", "zh-TW", "en", "en-US", "ja"]

        transcript = None
        try:
            # 1. 列出所有可用字幕
            transcript_list = self._api.list(video_id)

            available = [
                f"{t.language_code}({'auto' if t.is_generated else 'manual'})"
                for t in transcript_list
            ]
            logger.info(f"可用字幕轨道: {', '.join(available)}")

            # 2. 按优先级查找：先人工字幕，再自动字幕
            try:
                transcript = transcript_list.find_manually_created_transcript(langs)
                logger.info(
                    f"选中人工字幕: {transcript.language_code} ({transcript.language})"
                )
            except Exception:
                try:
                    transcript = transcript_list.find_generated_transcript(langs)
                    logger.info(
                        f"选中自动字幕: {transcript.language_code} ({transcript.language})"
                    )
                except Exception:
                    # 都没匹配，取第一个可用的
                    for candidate in transcript_list:
                        transcript = candidate
                        source = "auto" if candidate.is_generated else "manual"
                        logger.info(
                            f"使用首个可用字幕: {candidate.language_code} ({source})"
                        )
                        break

            if not transcript:
                logger.info(f"YouTube 视频 {video_id} 没有任何可用字幕")
                return None

            # 3. 获取字幕内容
            fetched = transcript.fetch()
            segments: List[TranscriptSegment] = []
            for snippet in fetched:
                # 优先属性（1.x dataclass），dict 兼容
                text = str(_snippet_value(snippet, "text", "") or "").strip()
                if not text:
                    continue
                try:
                    start = float(_snippet_value(snippet, "start", 0) or 0)
                except (TypeError, ValueError):
                    start = 0.0
                try:
                    duration = float(_snippet_value(snippet, "duration", 0) or 0)
                except (TypeError, ValueError):
                    duration = 0.0
                # duration 可能为 0（某些自动字幕），此时至少给一个极短区间，
                # 避免 end == start 让时间轴退化成一个点。
                end = start + duration if duration > 0 else start + 0.01
                segments.append(TranscriptSegment(start=start, end=end, text=text))

            if not segments:
                logger.warning(f"YouTube 字幕内容为空: {video_id}")
                return None

            full_text = " ".join(seg.text for seg in segments)
            logger.info(f"成功获取 YouTube 字幕，共 {len(segments)} 段")

            return TranscriptResult(
                language=transcript.language_code,
                full_text=full_text,
                segments=segments,
                raw={
                    "source": "youtube_transcript_api",
                    "language": transcript.language,
                    "language_code": transcript.language_code,
                    "is_generated": transcript.is_generated,
                },
            )

        except Exception as e:
            # 有轨道说明视频本身可访问，失败是网络/接口问题，值得 warning；
            # 没有轨道就是「这个视频没字幕」，info 级别即可。
            if transcript is not None:
                logger.warning(f"YouTube 字幕获取失败: {e}")
            else:
                logger.info(f"YouTube 字幕不可用: {e}")
            return None
