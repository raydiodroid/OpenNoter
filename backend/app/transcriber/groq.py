from abc import ABC
import os

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

class GroqTranscriber(Transcriber, ABC):


    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        provider = ProviderService.get_provider_by_id('groq')
        if not provider:
            raise Exception("Groq 供应商未配置,请配置以后使用。")
        client = OpenAI(
            api_key=provider.get('api_key'),
            base_url=provider.get('base_url')
        )
        filename = file_path

        with open(filename, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(filename, file.read()),
                model=os.getenv('GROQ_TRANSCRIBER_MODEL'),
                response_format="verbose_json",
            )
            print(transcription.text)
        print(transcription)
        segments = []
        full_text = ""

        for seg in transcription.segments:
            text = seg.text.strip()
            full_text += text + " "
            segments.append(TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=text
            ))

        result = TranscriptResult(
            language=transcription.language,
            full_text=full_text.strip(),
            segments=segments,
            raw=transcription.to_dict()
        )
        return result
