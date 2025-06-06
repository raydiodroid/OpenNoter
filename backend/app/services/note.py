import json
import logging
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple, Union, Any

from pydantic import HttpUrl
from dotenv import load_dotenv

from app.downloaders.base import Downloader
from app.services.constant import SUPPORT_PLATFORM_MAP
from app.enmus.task_status_enums import TaskStatus
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.db.video_task_dao import delete_task_by_video, insert_video_task
from app.gpt.base import GPT
from app.gpt.gpt_factory import GPTFactory
from app.models.audio_model import AudioDownloadResult
from app.models.gpt_model import GPTSource
from app.models.model_config import ModelConfig
from app.models.notes_model import NoteResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.transcriber.transcriber_provider import get_transcriber, _transcribers
from app.utils.video_reader import VideoReader
from app.utils.video_helper import generate_screenshot
from app.utils.note_helper import replace_content_markers
from app.enmus.note_enums import DownloadQuality

# 环境变量
load_dotenv()
NOTE_OUTPUT_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "/static/screenshots")
IMAGE_OUTPUT_DIR = os.getenv("OUT_DIR", "images")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NoteGenerator:

    class States:
        INIT = 'INIT'
        PARSING = 'PARSING'
        DOWNLOADING = 'DOWNLOADING'
        TRANSCRIBING = 'TRANSCRIBING'
        SUMMARIZING = 'SUMMARIZING'
        SAVING = 'SAVING'
        SUCCESS = 'SUCCESS'
        FAILED = 'FAILED'

    def __init__(self):
        self.transcriber_type = os.getenv("TRANSCRIBER_TYPE", "fast-whisper")
        self.transcriber: Transcriber = self._init_transcriber()
        self.video_img_urls = []

    @staticmethod
    def update_task_status(task_id: str, status: Union[str, TaskStatus], message: Optional[str] = None):
        os.makedirs(NOTE_OUTPUT_DIR, exist_ok=True)
        path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
        content = {"status": status.value if isinstance(status, TaskStatus) else status}
        if message:
            content["message"] = message
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

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

        self.task_id = task_id
        self._change_state(self.States.INIT)

        try:
            self._change_state(self.States.PARSING)

            downloader = self._get_downloader(platform)
            gpt = self._get_gpt(model_name, provider_id)

            self.audio_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_audio.json"
            self.transcript_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_transcript.json"
            self.markdown_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_markdown.md"

            self.audio_meta = self._download_audio_video(
                downloader, video_url, quality, output_path,
                screenshot, video_understanding, video_interval, grid_size or []
            )

            self.transcript = self._transcribe_audio()

            self.markdown = self._summarize_text(
                gpt, link, screenshot, _format or [], style, extras
            )

            self.markdown = self._post_process_markdown(
                self.markdown, self.video_path, _format or [], self.audio_meta, platform
            )

            self._change_state(self.States.SAVING)
            self._save_metadata(self.audio_meta.video_id, platform, task_id)

            self._change_state(self.States.SUCCESS)
            return NoteResult(markdown=self.markdown, transcript=self.transcript, audio_meta=self.audio_meta)

        except Exception as e:
            logger.exception(f"任务 {self.task_id} 失败: {e}")
            self._change_state(self.States.FAILED, str(e))
            return None

    def _change_state(self, state: str, message: Optional[str] = None):
        if not self.task_id:
            return
        NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        status_file = NOTE_OUTPUT_DIR / f"{self.task_id}.status.json"
        data = {"status": state}
        if message:
            data["message"] = message
        temp_file = status_file.with_suffix('.tmp')
        with temp_file.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_file.replace(status_file)

    def _init_transcriber(self) -> Transcriber:
        if self.transcriber_type not in _transcribers:
            raise Exception(f"不支持的转写器：{self.transcriber_type}")
        return get_transcriber(self.transcriber_type)

    def _get_gpt(self, model_name: Optional[str], provider_id: Optional[str]) -> GPT:
        provider = ProviderService.get_provider_by_id(provider_id)
        if not provider:
            raise ProviderError(code=ProviderErrorEnum.NOT_FOUND, message=ProviderErrorEnum.NOT_FOUND.message)
        config = ModelConfig(
            api_key=provider["api_key"], base_url=provider["base_url"],
            model_name=model_name, provider=provider["type"], name=provider["name"]
        )
        return GPTFactory().from_config(config)

    def _get_downloader(self, platform: str) -> Downloader:
        downloader_cls = SUPPORT_PLATFORM_MAP.get(platform)
        if not downloader_cls:
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        return downloader_cls

    def _download_audio_video(self, downloader, video_url, quality, output_path,
                               screenshot, video_understanding, video_interval, grid_size):
        self._change_state(self.States.DOWNLOADING)

        need_video = screenshot or video_understanding
        if need_video:
            self.video_path = Path(downloader.download_video(video_url, output_path))
            if grid_size:
                self.video_img_urls = VideoReader(
                    video_path=str(self.video_path),
                    grid_size=tuple(grid_size),
                    frame_interval=video_interval,
                    unit_width=1280, unit_height=720,
                    save_quality=90,
                ).run()

        if self.audio_cache_file.exists():
            with open(self.audio_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AudioDownloadResult(**data)

        audio = downloader.download(
            video_url=video_url, quality=quality, output_dir=output_path, need_video=need_video
        )
        with open(self.audio_cache_file, "w", encoding="utf-8") as f:
            json.dump(asdict(audio), f, ensure_ascii=False, indent=2)
        return audio

    def _transcribe_audio(self):
        self._change_state(self.States.TRANSCRIBING)
        if self.transcript_cache_file.exists():
            with open(self.transcript_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
            return TranscriptResult(language=data["language"], full_text=data["full_text"], segments=segments)

        transcript = self.transcriber.transcript(self.audio_meta.file_path)
        with open(self.transcript_cache_file, "w", encoding="utf-8") as f:
            json.dump(asdict(transcript), f, ensure_ascii=False, indent=2)
        return transcript

    def _summarize_text(self, gpt, link, screenshot, formats, style, extras):
        self._change_state(self.States.SUMMARIZING)
        source = GPTSource(
            title=self.audio_meta.title,
            segment=self.transcript.segments,
            tags=self.audio_meta.raw_info.get("tags", []),
            screenshot=screenshot,
            video_img_urls=self.video_img_urls,
            link=link, _format=formats, style=style, extras=extras
        )
        markdown = gpt.summarize(source)
        with open(self.markdown_cache_file, "w", encoding="utf-8") as f:
            f.write(markdown)
        return markdown

    def _post_process_markdown(self, markdown, video_path, formats, audio_meta, platform):
        if "screenshot" in formats and video_path:
            markdown = self._insert_screenshots(markdown, video_path)
        if "link" in formats:
            markdown = replace_content_markers(markdown, video_id=audio_meta.video_id, platform=platform)
        return markdown

    def _insert_screenshots(self, markdown, video_path):
        pattern = r"(?:\*Screenshot-(\d{2}):(\d{2})|Screenshot-\[(\d{2}):(\d{2})\])"
        matches = []
        for match in re.finditer(pattern, markdown):
            mm = match.group(1) or match.group(3)
            ss = match.group(2) or match.group(4)
            matches.append((match.group(0), int(mm)*60+int(ss)))
        for idx, (marker, ts) in enumerate(matches):
            img_path = generate_screenshot(str(video_path), str(IMAGE_OUTPUT_DIR), ts, idx)
            filename = Path(img_path).name
            img_url = f"{IMAGE_BASE_URL.rstrip('/')}/{filename}"
            markdown = markdown.replace(marker, f"![]({img_url})", 1)
        return markdown

    def _save_metadata(self, video_id: str, platform: str, task_id: str):
        insert_video_task(video_id=video_id, platform=platform, task_id=task_id)

    @staticmethod
    def delete_note(video_id: str, platform: str) -> int:
        return delete_task_by_video(video_id, platform)