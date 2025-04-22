from app.gpt.base import GPT
from app.models.gpt_model import GPTSource
from app.gpt.prompt import BASE_PROMPT, AI_SUM, SCREENSHOT, LINK
from app.gpt.utils import fix_markdown
from app.models.transcriber_model import TranscriptSegment
from datetime import timedelta
from typing import List

class UniversalGPT(GPT):
    def __init__(self, client, model: str, temperature: float = 0.7):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.screenshot = False
        self.screenshot = False
        self.link = False

    def _format_time(self, seconds: float) -> str:
        return str(timedelta(seconds=int(seconds)))[2:]

    def _build_segment_text(self, segments: List[TranscriptSegment]) -> str:
        return "\n".join(
            f"{self._format_time(seg.start)} - {seg.text.strip()}"
            for seg in segments
        )

    def ensure_segments_type(self, segments) -> List[TranscriptSegment]:
        return [TranscriptSegment(**seg) if isinstance(seg, dict) else seg for seg in segments]

    def create_messages(self, segments: List[TranscriptSegment],**kwargs):
        content = BASE_PROMPT.format(
            video_title=kwargs.get('title'),
            segment_text=self._build_segment_text(segments),
            tags=kwargs.get('tags')
        )
        if self.screenshot:
            print(":需要截图")
            content += SCREENSHOT
        if self.link:
            print(":需要链接")
            content += LINK

        print(content)
        return [{"role": "user", "content": content + AI_SUM}]

    def list_models(self):
        return self.client.list_models()
    def summarize(self, source: GPTSource) -> str:
        self.screenshot = source.screenshot
        self.link = source.link
        source.segment = self.ensure_segments_type(source.segment)
        messages = self.create_messages(source.segment, source.title,source.tags)
        response = self.client.chat(
            model=self.model,
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()

if __name__ == '__main__':
    print('s')

