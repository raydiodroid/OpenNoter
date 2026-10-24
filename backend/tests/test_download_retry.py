"""NoteGenerator 主流程：任务级重试、失败分类、缓存指纹、Cookie 提示。

覆盖本轮改造的 7 条待做事项里可单测的部分：

1. 任务级重试：只重试 `is_retryable()` 为 True 的异常，指数退避 1/2/4s，
   期间状态消息带 `重试 n/N`，重试次数耗尽或不可重试时一次就失败；
2. 缓存 key 带 `url + quality` 指纹（修「前端重试复用 task_id 会命中旧音频缓存」），
   同时保留旧命名缓存的只读兼容；
3. `skip_download` 回退路径记 info 而不是 warning；
4. 下载得到的媒体文件必须存在且非空，否则抛 `RetryableDownloadError`；
5. `_handle_exception` 对超大小/超时长给中文可读消息；
6. `routers/config.py` 的 Cookie 提示与 count 计算按平台格式区分。

全部用 monkeypatch 模拟下载器 / GPT，不依赖真实网络。
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

import pytest

from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)
from app.downloaders.limits import DownloadLimits
from app.enmus.note_enums import DownloadQuality
from app.enmus.task_status_enums import TaskStatus
from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services import note as note_module
from app.services.note import NoteCachePaths, NoteGenerator


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class FakeDownloader:
    """最小可用的下载器替身，按脚本抛出/返回，并记录每次调用。"""

    def __init__(
        self,
        *,
        audio_results=None,
        subtitle_result=None,
        audio_errors=None,
        subtitle_errors=None,
        skip_download_error=None,
        video_path=None,
    ):
        self.audio_results = list(audio_results or [])
        self.subtitle_result = subtitle_result
        self.audio_errors = list(audio_errors or [])
        self.subtitle_errors = list(subtitle_errors or [])
        self.skip_download_error = skip_download_error
        self.video_path = video_path
        self.download_calls = []
        self.subtitle_calls = 0
        self.video_calls = 0

    def download_subtitles(self, video_url, output_dir=None, langs=None):
        self.subtitle_calls += 1
        if self.subtitle_errors:
            raise self.subtitle_errors.pop(0)
        return self.subtitle_result

    def download(self, video_url, output_dir=None, quality="medium", need_video=False, skip_download=False):
        self.download_calls.append({"skip_download": skip_download, "quality": quality})
        if skip_download and self.skip_download_error is not None:
            raise self.skip_download_error
        if self.audio_errors:
            raise self.audio_errors.pop(0)
        if not self.audio_results:
            raise AssertionError("FakeDownloader 没有配置可返回的 audio_results")
        return self.audio_results.pop(0)

    def download_video(self, video_url, output_dir=None):
        self.video_calls += 1
        return self.video_path or str(_TMP_VIDEO[0])


class FakeGPT:
    def __init__(self, markdown="# 笔记"):
        self.markdown = markdown
        self.sources = []

    def summarize(self, source):
        self.sources.append(source)
        return self.markdown


class FakeTranscriber:
    """转写器替身：不真的跑 whisper。"""

    def __init__(self, transcript=None):
        self.transcript_result = transcript or _make_transcript()
        self.calls = 0

    def transcript(self, file_path):
        self.calls += 1
        return self.transcript_result


# 视频下载替身需要一个真实存在的路径（Downloader.download_video 的契约）
_TMP_VIDEO = [None]


def _make_audio(file_path: Path, *, title="测试视频", video_id="BV1test") -> AudioDownloadResult:
    return AudioDownloadResult(
        file_path=str(file_path),
        title=title,
        duration=123.0,
        cover_url=None,
        platform="bilibili",
        video_id=video_id,
        raw_info={"tags": []},
        video_path=None,
    )


def _make_transcript(text="这是字幕") -> TranscriptResult:
    return TranscriptResult(
        language="zh",
        full_text=text,
        segments=[TranscriptSegment(start=0.0, end=1.5, text=text)],
    )


@pytest.fixture()
def output_dir(tmp_path, monkeypatch):
    """把缓存/状态文件都写到 tmp 目录，避免污染仓库里的 note_results/。"""
    monkeypatch.setattr(note_module, "NOTE_OUTPUT_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """重试退避不真的 sleep（通过替换实例方法实现，见 _make_generator）。"""
    yield


def _make_generator(monkeypatch, *, limits=None, downloader=None, gpt=None, task_id="task-1"):
    """构造一个跳过 __init__（不加载真转写器/模型）的 NoteGenerator。"""
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.video_path = None
    gen.video_img_urls = []
    gen.limits = limits or DownloadLimits(max_size_mb=200, max_duration_seconds=300, retry_count=2)
    gen.transcriber = FakeTranscriber()
    gen.sleeps = []

    def _fake_sleep(attempt):
        gen.sleeps.append(gen._retry_delay(attempt))

    gen._sleep_before_retry = _fake_sleep

    if downloader is not None:
        gen._get_downloader = lambda platform: downloader
    if gpt is not None:
        gen._get_gpt = lambda model_name, provider_id: gpt
    return gen


def _read_status(output_dir: Path, task_id: str) -> dict:
    return json.loads((output_dir / f"{task_id}.status.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. 任务级重试
# ---------------------------------------------------------------------------
def test_retryable_error_is_retried_then_succeeds(output_dir, monkeypatch):
    """可重试异常：重试 retry_count 次后成功，任务 SUCCESS。"""
    media = output_dir / "audio.mp3"
    media.write_bytes(b"fake-audio")

    downloader = FakeDownloader(
        audio_errors=[RetryableDownloadError("网络抖动"), RetryableDownloadError("CDN 5xx")],
        subtitle_result=None,
        audio_results=[_make_audio(media)],
    )
    gpt = FakeGPT()
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=2),
        downloader=downloader,
        gpt=gpt,
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        quality=DownloadQuality.medium,
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is not None
    # 成功路径会在开头补一行来源链接，断言核心内容在即可
    assert "# 笔记" in result.markdown
    # 2 次失败 + 1 次成功 = 3 次尝试
    assert len(downloader.download_calls) == 3
    # 指数退避 2**0 = 1s, 2**1 = 2s
    assert gen.sleeps == [1, 2]
    assert _read_status(output_dir, "task-1")["status"] == TaskStatus.SUCCESS.value


def test_retry_updates_status_message_with_attempt_counter(output_dir, monkeypatch):
    """每次重试都要落一条「（重试 n/N）」状态消息，让前端能显示。"""
    media = output_dir / "audio.mp3"
    media.write_bytes(b"fake-audio")

    downloader = FakeDownloader(
        audio_errors=[RetryableDownloadError("第一次失败")],
        audio_results=[_make_audio(media)],
    )
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    # 用 spies 记录每次 _update_status 的 message
    messages = []
    original_update = gen._update_status

    def _spy(task_id, status, message=None, user_id=None):
        if message:
            messages.append(message)
        return original_update(task_id, status, message=message, user_id=user_id)

    gen._update_status = _spy

    gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert any("重试 1/2" in m for m in messages), messages
    # 状态文件最终被 SUCCESS 覆盖
    assert _read_status(output_dir, "task-1")["status"] == TaskStatus.SUCCESS.value


def test_retry_exhausted_marks_task_failed(output_dir, monkeypatch):
    """重试次数耗尽后任务 FAILED，且尝试次数 = retry_count + 1。"""
    downloader = FakeDownloader(
        audio_errors=[RetryableDownloadError(f"失败{i}") for i in range(10)],
    )
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    assert len(downloader.download_calls) == 3  # 1 次原始 + 2 次重试
    status = _read_status(output_dir, "task-1")
    assert status["status"] == TaskStatus.FAILED.value
    assert "失败2" in status["message"]


def test_retry_count_zero_means_single_attempt(output_dir, monkeypatch):
    """retry_count=0 时只尝试一次，不 sleep。"""
    downloader = FakeDownloader(audio_errors=[RetryableDownloadError("挂了")])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=0),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert len(downloader.download_calls) == 1
    assert gen.sleeps == []


def test_size_limit_error_is_not_retried(output_dir, monkeypatch):
    """不可重试异常（超大小限制）：只尝试一次，立刻失败。"""
    downloader = FakeDownloader(
        audio_errors=[SizeLimitExceeded("too big"), RetryableDownloadError("不该被调用")]
    )
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=5),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    assert len(downloader.download_calls) == 1
    assert gen.sleeps == []


def test_duration_limit_error_is_not_retried(output_dir, monkeypatch):
    downloader = FakeDownloader(audio_errors=[DurationLimitExceeded("too long")])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=3),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    assert len(downloader.download_calls) == 1


@pytest.mark.parametrize("attempt,expected", [(0, 1), (1, 2), (2, 4), (3, 8)])
def test_retry_backoff_is_exponential(attempt, expected):
    gen = NoteGenerator.__new__(NoteGenerator)
    assert gen._retry_delay(attempt) == expected


def test_retry_message_does_not_leak_into_later_states(output_dir):
    """「（重试 n/N）」是临时提示，阶段推进后必须被清掉。

    不清的话会一路带到 SUMMARIZING / SUCCESS，前端会显示「完成（重试 2/2）」。
    """
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()

    gen._update_status("task-1", TaskStatus.DOWNLOADING, message="下载中（重试 1/2）")
    assert "重试 1/2" in _read_status(output_dir, "task-1")["message"]

    gen._update_status("task-1", TaskStatus.SUMMARIZING)
    status = _read_status(output_dir, "task-1")
    assert status["status"] == TaskStatus.SUMMARIZING.value
    assert "message" not in status or "重试" not in status.get("message", "")

    gen._update_status("task-1", TaskStatus.SUCCESS)
    status = _read_status(output_dir, "task-1")
    assert status["status"] == TaskStatus.SUCCESS.value
    assert "message" not in status or "重试" not in status.get("message", "")


def test_retry_message_is_preserved_when_repeated(output_dir):
    """连续多次重试时，每一条新的重试消息仍要写进去（不能被清理逻辑吃掉）。"""
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()

    gen._update_status("task-1", TaskStatus.DOWNLOADING, message="下载中（重试 1/2）")
    gen._update_status("task-1", TaskStatus.DOWNLOADING, message="下载中（重试 2/2）")
    assert "重试 2/2" in _read_status(output_dir, "task-1")["message"]


def test_real_failure_message_is_not_treated_as_retry_message(output_dir):
    """正常失败消息不能被「清理重试提示」的逻辑误删。"""
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()

    gen._update_status("task-1", TaskStatus.FAILED, message="视频超过 200MB 大小限制")
    gen._update_status("task-1", TaskStatus.FAILED)
    assert "200MB" in _read_status(output_dir, "task-1")["message"]


def test_task_is_not_marked_failed_while_retrying(output_dir, monkeypatch):
    """重试过程中状态文件不能出现 FAILED。

    前端一旦轮询到 FAILED 就停止等待并弹错误，后面的重试即使成功也白费。
    所以重试期间只允许出现「<阶段>（重试 n/N）」，FAILED 只能在最终放弃时写一次。
    """
    media = output_dir / "audio.mp3"
    media.write_bytes(b"fake-audio")
    downloader = FakeDownloader(
        audio_errors=[RetryableDownloadError("抖动1"), RetryableDownloadError("抖动2")],
        audio_results=[_make_audio(media)],
    )
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    seen_statuses = []
    original_update = gen._update_status

    def _spy(task_id, status, message=None, user_id=None):
        seen_statuses.append(status.value if isinstance(status, TaskStatus) else status)
        return original_update(task_id, status, message=message, user_id=user_id)

    gen._update_status = _spy

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is not None
    assert TaskStatus.FAILED.value not in seen_statuses, seen_statuses
    assert seen_statuses[-1] == TaskStatus.SUCCESS.value


# ---------------------------------------------------------------------------
# 2. 缓存指纹
# ---------------------------------------------------------------------------
def test_fingerprint_changes_with_url_and_quality(output_dir):
    """缓存 key 必须随 url / quality 变化（否则前端重试改了链接会命中旧音频）。"""
    base = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    other_url = NoteCachePaths.build("task-1", "https://example.com/b", DownloadQuality.medium)
    other_quality = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.fast)

    assert base.audio != other_url.audio
    assert base.transcript != other_url.transcript
    assert base.markdown != other_url.markdown
    assert base.audio != other_quality.audio
    assert base.transcript != other_quality.transcript


def test_fingerprint_is_stable_for_same_input(output_dir):
    first = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    second = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    assert first.audio == second.audio
    assert first.transcript == second.transcript
    assert first.markdown == second.markdown


def test_fingerprint_supports_enum_and_raw_string(output_dir):
    """generate() 收到的是枚举，但 DownloadQuality 是 str 枚举，两种写法必须等价。"""
    from_enum = NoteCachePaths.build("t", "https://example.com/a", DownloadQuality.slow)
    from_str = NoteCachePaths.build("t", "https://example.com/a", "slow")
    assert from_enum.audio == from_str.audio


def test_cache_filename_contains_task_id_and_fingerprint(output_dir):
    cache = NoteCachePaths.build("task-abc", "https://example.com/a", DownloadQuality.fast)
    name = cache.audio.name
    assert name.startswith("task-abc_")
    assert cache.fingerprint in name
    assert name.endswith("_audio.json")
    # 新 key 与老 key 必须是不同的文件
    assert cache.audio != cache.legacy_audio
    assert cache.transcript != cache.legacy_transcript
    assert cache.markdown != cache.legacy_markdown


def test_legacy_transcript_cache_is_still_readable(output_dir):
    """向后兼容：只有老命名的缓存时也能读到。"""
    transcript = _make_transcript("老缓存字幕")
    legacy = output_dir / "task-1_transcript.json"
    legacy.write_text(json.dumps(asdict(transcript), ensure_ascii=False), encoding="utf-8")

    cache = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    assert not cache.transcript.exists()

    gen = NoteGenerator.__new__(NoteGenerator)
    loaded = gen._load_transcript_cache(cache, task_id="task-1")

    assert loaded is not None
    assert loaded.full_text == "老缓存字幕"


def test_new_transcript_cache_wins_over_legacy(output_dir):
    """新旧缓存同时存在时，优先读带指纹的新 key（写只写新 key）。"""
    new_transcript = _make_transcript("新缓存字幕")
    old_transcript = _make_transcript("老缓存字幕")
    cache = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    cache.transcript.write_text(json.dumps(asdict(new_transcript), ensure_ascii=False), encoding="utf-8")
    cache.legacy_transcript.write_text(json.dumps(asdict(old_transcript), ensure_ascii=False), encoding="utf-8")

    gen = NoteGenerator.__new__(NoteGenerator)
    loaded = gen._load_transcript_cache(cache, task_id="task-1")

    assert loaded.full_text == "新缓存字幕"


def test_download_media_writes_new_key_only(output_dir, monkeypatch):
    """写完缓存后，只应产生带指纹的新 key，不产生老 key。"""
    media = output_dir / "audio.mp3"
    media.write_bytes(b"fake-audio")
    downloader = FakeDownloader(audio_results=[_make_audio(media)])

    gen = NoteGenerator.__new__(NoteGenerator)
    gen.video_path = None
    gen.video_img_urls = []
    gen.limits = DownloadLimits()
    gen.sleeps = []
    gen._sleep_before_retry = lambda attempt: gen.sleeps.append(attempt)

    cache = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    audio = gen._download_media(
        downloader=downloader,
        video_url="https://example.com/a",
        quality=DownloadQuality.medium,
        task_id="task-1",
        cache=cache,
        status_phase=TaskStatus.DOWNLOADING,
        platform="bilibili",
        output_path=None,
        screenshot=False,
        video_understanding=False,
        video_interval=0,
        grid_size=[],
        skip_download=False,
    )

    assert audio is not None
    assert cache.audio.exists()
    assert not cache.legacy_audio.exists()


def test_download_media_reads_legacy_audio_cache(output_dir):
    """老命名的音频缓存仍能命中，避免历史任务白白重下。"""
    audio = _make_audio(output_dir / "whatever.mp3", title="老缓存标题")
    legacy = output_dir / "task-1_audio.json"
    legacy.write_text(json.dumps(asdict(audio), ensure_ascii=False), encoding="utf-8")

    gen = NoteGenerator.__new__(NoteGenerator)
    gen.video_path = None
    gen.video_img_urls = []
    gen.limits = DownloadLimits()

    downloader = FakeDownloader()  # 一旦被调用就会 AssertionError，证明走了缓存
    cache = NoteCachePaths.build("task-1", "https://example.com/a", DownloadQuality.medium)
    loaded = gen._download_media(
        downloader=downloader,
        video_url="https://example.com/a",
        quality=DownloadQuality.medium,
        task_id="task-1",
        cache=cache,
        status_phase=TaskStatus.DOWNLOADING,
        platform="bilibili",
        output_path=None,
        screenshot=False,
        video_understanding=False,
        video_interval=0,
        grid_size=[],
    )

    assert loaded.title == "老缓存标题"
    assert downloader.download_calls == []


# ---------------------------------------------------------------------------
# 3. skip_download 回退路径的日志级别
# ---------------------------------------------------------------------------
def test_subtitle_path_uses_skip_download_and_avoids_full_media(output_dir):
    """有平台字幕且不需要截图 → 走 skip_download 捷径，不下载媒体文件。"""
    downloader = FakeDownloader(
        subtitle_result=_make_transcript("平台字幕"),
        audio_results=[
            AudioDownloadResult(
                file_path=str(output_dir / "meta-only.m4a"),  # skip_download 下该文件本就不存在
                title="字幕视频",
                duration=60.0,
                cover_url=None,
                platform="bilibili",
                video_id="BV1test",
                raw_info={"tags": []},
                video_path=None,
            )
        ],
    )
    gen = _make_generator(
        monkeypatch=None,
        limits=DownloadLimits(),
        downloader=downloader,
        gpt=FakeGPT(),
    )
    gen._sleep_before_retry = lambda attempt: None

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is not None
    # 唯一一次 download 调用必须是 skip_download=True（不能回退成完整下载）
    assert len(downloader.download_calls) == 1
    assert downloader.download_calls[0]["skip_download"] is True
    # 有字幕就不该再调转写器
    assert gen.transcriber.calls == 0


def test_skip_download_mode_does_not_validate_file_existence(output_dir):
    """skip_download 只取元信息，file_path 不存在是正常的，不能因此判失败。

    这条是给 yt-dlp 那条「有字幕就不下音频」的捷径兜底的：元信息路径本来就
    不会产生文件，如果这里也去校验存在性，所有有字幕的视频都会失败。
    """
    metadata_only = AudioDownloadResult(
        file_path=str(output_dir / "never-created.m4a"),
        title="只有元信息",
        duration=30.0,
        cover_url=None,
        platform="bilibili",
        video_id="BV1test",
        raw_info={"tags": []},
        video_path=None,
    )
    downloader = FakeDownloader(
        subtitle_result=_make_transcript("平台字幕"),
        audio_results=[metadata_only],
    )
    gen = _make_generator(
        monkeypatch=None,
        limits=DownloadLimits(),
        downloader=downloader,
        gpt=FakeGPT(),
    )
    gen._sleep_before_retry = lambda attempt: None

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is not None
    assert result.audio_meta.title == "只有元信息"
    assert not (output_dir / "never-created.m4a").exists()


def test_skip_download_fallback_logs_info_not_warning(output_dir, caplog):
    """skip_download 不支持时回退完整下载，日志应是 info 而不是 warning。"""
    media = output_dir / "audio.mp3"
    media.write_bytes(b"fake-audio")
    downloader = FakeDownloader(
        subtitle_result=_make_transcript(),  # 有字幕 → 走 skip_download 捷径
        skip_download_error=TypeError("unexpected keyword argument 'skip_download'"),
        audio_results=[_make_audio(media)],
    )
    gen = _make_generator(
        monkeypatch=None,
        limits=DownloadLimits(),
        downloader=downloader,
        gpt=FakeGPT(),
    )
    gen._sleep_before_retry = lambda attempt: None

    with caplog.at_level(logging.INFO, logger="app.services.note"):
        gen.generate(
            video_url="https://www.bilibili.com/video/BV1test",
            platform="bilibili",
            task_id="task-1",
            model_name="m",
            provider_id="p",
        )

    records = [r for r in caplog.records if r.name == "app.services.note"]
    fallback_records = [r for r in records if "回退为完整下载" in r.getMessage()]
    assert fallback_records, [r.getMessage() for r in records]
    # 这条回退信息不能被记成 warning/error
    assert all(r.levelno == logging.INFO for r in fallback_records)
    # 且确实回退去下了完整音频
    assert any(call["skip_download"] is False for call in downloader.download_calls)


def test_limit_error_during_metadata_only_is_not_fallen_back(output_dir, monkeypatch):
    """skip_download 阶段就触发上限时，不能回退去完整下载（只会再撞一次同一个上限）。"""
    downloader = FakeDownloader(
        subtitle_result=_make_transcript("平台字幕"),
        skip_download_error=SizeLimitExceeded("too big"),
    )
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(max_size_mb=200, retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    # 只调了一次（skip_download 那次），没有第二次完整下载
    assert len(downloader.download_calls) == 1
    assert downloader.download_calls[0]["skip_download"] is True
    assert gen.sleeps == []  # 不可重试
    assert "200MB" in _read_status(output_dir, "task-1")["message"]


# ---------------------------------------------------------------------------
# 4. 下载文件存在性校验
# ---------------------------------------------------------------------------
def test_missing_media_file_raises_retryable_error(output_dir, monkeypatch):
    """下载器返回的路径不存在 → RetryableDownloadError（可重试），不是静默通过。"""
    missing = output_dir / "not-there.mp3"
    downloader = FakeDownloader(audio_results=[_make_audio(missing) for _ in range(3)])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    # 残文件是可重试的：1 次原始 + 2 次重试
    assert len(downloader.download_calls) == 3
    status = _read_status(output_dir, "task-1")
    assert status["status"] == TaskStatus.FAILED.value
    assert "音频文件未生成" in status["message"]


def test_empty_media_file_raises_retryable_error(output_dir):
    empty = output_dir / "empty.mp3"
    empty.write_bytes(b"")
    audio = _make_audio(empty)

    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()

    with pytest.raises(RetryableDownloadError) as excinfo:
        gen._assert_media_file_ready(audio, video_url="https://example.com/a")
    assert "音频文件为空" in str(excinfo.value)


def test_non_empty_media_file_passes_validation(output_dir):
    media = output_dir / "ok.m4a"  # 扩展名不影响判定，只看存在 + 非空
    media.write_bytes(b"x")
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()
    # 不抛异常即通过
    gen._assert_media_file_ready(_make_audio(media), video_url="https://example.com/a")


def test_audio_without_file_path_is_retryable_error():
    audio = _make_audio(Path("/tmp/whatever.mp3"))
    audio.file_path = ""
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()
    with pytest.raises(RetryableDownloadError):
        gen._assert_media_file_ready(audio, video_url="https://example.com/a")


def test_missing_file_with_oversize_metadata_raises_size_limit(output_dir):
    """yt-dlp max_filesize 静默跳过时返回 0 退出码、无文件。

    如果返回值里带了超限的 filesize，应判为不可重试的 SizeLimitExceeded，
    而不是让整条任务白白重试 retry_count 次。
    """
    audio = _make_audio(output_dir / "not-created.mp4")
    audio.raw_info = {"filesize_approx": 500 * 1024 * 1024}  # 500MB > 200MB
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits(max_size_mb=200)

    with pytest.raises(SizeLimitExceeded) as excinfo:
        gen._assert_media_file_ready(audio, video_url="https://example.com/a")
    assert "200MB" in str(excinfo.value)


def test_missing_file_with_oversize_duration_raises_duration_limit(output_dir):
    audio = _make_audio(output_dir / "not-created.mp4")
    audio.duration = 1000.0  # 1000s > 300s
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits(max_duration_seconds=300)

    with pytest.raises(DurationLimitExceeded):
        gen._assert_media_file_ready(audio, video_url="https://example.com/a")


def test_max_filesize_skip_propagates_as_size_limit_without_retry(output_dir, monkeypatch):
    """端到端：下载器「成功返回」了不存在的路径 + 超限元信息 → 一次就失败。"""
    from app.downloaders.errors import SizeLimitExceeded as S

    audio = _make_audio(output_dir / "skipped.mp4")
    audio.raw_info = {"filesize_approx": 800 * 1024 * 1024}
    downloader = FakeDownloader(audio_results=[audio])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(max_size_mb=200, retry_count=3),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    assert result is None
    assert len(downloader.download_calls) == 1, "超限不该重试"
    assert gen.sleeps == []
    message = _read_status(output_dir, "task-1")["message"]
    assert "200MB" in message


# ---------------------------------------------------------------------------
# 5. 失败消息可读
# ---------------------------------------------------------------------------
def test_size_limit_failure_message_is_user_readable(output_dir, monkeypatch):
    downloader = FakeDownloader(audio_errors=[SizeLimitExceeded("raw english")])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(max_size_mb=200, retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    message = _read_status(output_dir, "task-1")["message"]
    assert "200MB" in message
    assert "DOWNLOAD_MAX_SIZE_MB" in message
    assert "raw english" not in message


def test_duration_limit_failure_message_is_user_readable(output_dir, monkeypatch):
    downloader = FakeDownloader(audio_errors=[DurationLimitExceeded("raw english")])
    gen = _make_generator(
        monkeypatch,
        limits=DownloadLimits(max_duration_seconds=300, retry_count=2),
        downloader=downloader,
        gpt=FakeGPT(),
    )

    gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="task-1",
        model_name="m",
        provider_id="p",
    )

    message = _read_status(output_dir, "task-1")["message"]
    assert "300" in message
    assert "秒" in message
    assert "DOWNLOAD_MAX_DURATION_SECONDS" in message


def test_generic_failure_message_keeps_original_text(output_dir):
    gen = NoteGenerator.__new__(NoteGenerator)
    gen.limits = DownloadLimits()
    assert gen._friendly_error_message(RuntimeError("随便一个错误")) == "随便一个错误"


def test_transcript_cache_path_helper_matches_new_key(output_dir):
    """对外暴露的路径助手必须和新 key 完全一致（否则预取字幕会写错地方）。"""
    path = note_module.transcript_cache_path("task-9", "https://example.com/a", DownloadQuality.medium)
    cache = NoteCachePaths.build("task-9", "https://example.com/a", DownloadQuality.medium)
    assert path == cache.transcript
    assert path != cache.legacy_transcript


# ---------------------------------------------------------------------------
# 6. routers/config.py 的 Cookie 提示与 count
# ---------------------------------------------------------------------------
NETSCAPE_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tsecret\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tHSID\tsecret2\n"
)


def _load_config_router():
    import app.routers.config as config_router

    return config_router


def test_update_cookie_hint_for_netscape_platform(monkeypatch):
    """YouTube 是 netscape 格式：过滤后为空要说「没有有效的 Netscape 条目」。"""
    config = _load_config_router()
    saved = {}
    monkeypatch.setattr(config.cookie_manager, "set", lambda platform, value: saved.update({platform: value}))

    class Data:
        # 只有注释行 → 规范化后为空
        platform = "youtube"
        cookie = "# just a comment\n"

    with pytest.raises(Exception) as excinfo:
        config.update_cookie(Data(), admin=None)
    detail = getattr(excinfo.value, "detail", str(excinfo.value))
    assert "Netscape" in detail
    assert "不使用 Cookie" not in detail
    assert saved == {}


def test_update_cookie_hint_for_header_platform_without_allowlist_items(monkeypatch):
    config = _load_config_router()
    monkeypatch.setattr(config.cookie_manager, "set", lambda platform, value: None)

    class Data:
        platform = "bilibili"
        cookie = "tracking=remove"

    with pytest.raises(Exception) as excinfo:
        config.update_cookie(Data(), admin=None)
    detail = getattr(excinfo.value, "detail", str(excinfo.value))
    assert "没有该平台下载器需要的登录项" in detail


def test_update_cookie_accepts_netscape_and_stores_it(monkeypatch):
    config = _load_config_router()
    saved = {}
    monkeypatch.setattr(config.cookie_manager, "set", lambda platform, value: saved.update({platform: value}))

    class Data:
        platform = "youtube"
        cookie = NETSCAPE_COOKIES

    config.update_cookie(Data(), admin=None)
    assert "youtube" in saved
    assert "Netscape HTTP Cookie File" in saved["youtube"]
    assert "SID" in saved["youtube"]


def test_complete_transfer_count_uses_netscape_line_count(monkeypatch):
    """netscape 格式的 count 必须按行统计（老实现按 '; ' 切会算成 1）。"""
    config = _load_config_router()

    class FakeTransfer:
        platform = "youtube"

    class FakeManager:
        def consume(self, ticket, origin, cookie):
            return FakeTransfer()

    saved = {}
    monkeypatch.setattr(config, "cookie_transfer_manager", FakeManager())
    monkeypatch.setattr(config.cookie_manager, "set", lambda platform, value: saved.update({platform: value}))

    class Data:
        transfer_id = "x" * 30
        page_origin = "https://example.com"
        cookie = NETSCAPE_COOKIES

    response = config.complete_browser_cookie_transfer(Data())
    payload = json.loads(response.body)
    assert payload["data"]["count"] == 2
    assert payload["data"]["platform"] == "youtube"
    assert "youtube" in saved


def test_complete_transfer_count_for_header_platform(monkeypatch):
    """header 平台的 count 仍然按 `; ` 条目数统计。"""
    config = _load_config_router()

    class FakeTransfer:
        platform = "bilibili"

    class FakeManager:
        def consume(self, ticket, origin, cookie):
            return FakeTransfer()

    monkeypatch.setattr(config, "cookie_transfer_manager", FakeManager())
    monkeypatch.setattr(config.cookie_manager, "set", lambda platform, value: None)

    class Data:
        transfer_id = "x" * 30
        page_origin = "https://example.com"
        cookie = "SESSDATA=a; bili_jct=b; buvid3=c"

    response = config.complete_browser_cookie_transfer(Data())
    payload = json.loads(response.body)
    assert payload["data"]["count"] == 3


def test_complete_transfer_hint_for_youtube_pasted_header_format(monkeypatch):
    """YouTube 贴成 header 形式时，报错要说清需要 cookies.txt 格式。"""
    config = _load_config_router()

    class FakeTransfer:
        platform = "youtube"

    class FakeManager:
        def consume(self, ticket, origin, cookie):
            return FakeTransfer()

    monkeypatch.setattr(config, "cookie_transfer_manager", FakeManager())

    class Data:
        transfer_id = "x" * 30
        page_origin = "https://example.com"
        cookie = "PREF=xyz"

    with pytest.raises(Exception) as excinfo:
        config.complete_browser_cookie_transfer(Data())
    detail = getattr(excinfo.value, "detail", str(excinfo.value))
    assert "Netscape" in detail
