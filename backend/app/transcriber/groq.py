from abc import ABC
import os

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.transcriber.base import Transcriber
from app.utils.openai_client import build_openai_client
import ffmpeg
import tempfile
from dotenv import load_dotenv
load_dotenv()
MAX_SIZE_MB = 18
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
def compress_audio(input_path: str, target_bitrate='64k') -> str:
    output_fd, output_path = tempfile.mkstemp(suffix=".mp3")  # 临时输出文件
    os.close(output_fd)  # 关闭文件描述符，ffmpeg 会用路径操作
    try:
        ffmpeg.input(input_path).output(output_path, audio_bitrate=target_bitrate).run(quiet=True, overwrite_output=True)
        return output_path
    except Exception:
        os.unlink(output_path)
        raise


def _resolve_groq_model() -> str:
    """在线转写用的模型名。

    优先取「音频转写配置」页保存到 config/transcriber.json 的值，
    回落到环境变量 GROQ_TRANSCRIBER_MODEL，保证老部署无需改动。
    """
    try:
        from app.services.transcriber_config_manager import TranscriberConfigManager

        value = TranscriberConfigManager().get_config().get("groq_model")
        if value:
            return value
    except Exception:
        pass
    return os.getenv("GROQ_TRANSCRIBER_MODEL", "")

class GroqTranscriber(Transcriber, ABC):


    def __init__(self, credential=None):
        self.credential = credential

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        file_size = os.path.getsize(file_path)
        from app.services.credential_resolver import resolve_asr
        credential = self.credential or resolve_asr(None, online=True)
        client = build_openai_client(
            api_key=credential.api_key, base_url=credential.base_url,
            key_label="音频转写 API Key", timeout=180.0, max_retries=1,
        )
        compressed = None
        try:
            if file_size > MAX_SIZE_BYTES:
                compressed = compress_audio(file_path)
                file_path = compressed
            filename = file_path
            with open(filename, "rb") as file:
                transcription = client.audio.transcriptions.create(
                    file=(filename, file.read()), model=credential.model_name,
                    response_format="verbose_json",
                )
        finally:
            client.close()
            if compressed:
                try:
                    os.unlink(compressed)
                except OSError:
                    pass
        segments = []
        full_text = ""

        for seg in (getattr(transcription, "segments", None) or []):
            text = seg.text.strip()
            full_text += text + " "
            segments.append(TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=text
            ))

        result = TranscriptResult(
            language=getattr(transcription, "language", "unknown"),
            full_text=full_text.strip() or transcription.text,
            segments=segments,
            raw=transcription.to_dict()
        )
        return result
