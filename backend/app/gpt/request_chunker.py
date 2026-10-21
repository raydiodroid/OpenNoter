from dataclasses import dataclass
import os
from typing import Callable, List, Optional


@dataclass
class ChunkPayload:
    segments: list
    image_urls: list


class RequestChunker:
    def __init__(
        self,
        message_builder: Callable,
        max_bytes: int,
        size_estimator: Optional[Callable] = None,
        max_images_per_chunk: Optional[int] = None,
    ):
        self.message_builder = message_builder
        self.max_bytes = max_bytes
        self.size_estimator = size_estimator
        configured_limit = max_images_per_chunk
        if configured_limit is None:
            configured_limit = int(os.getenv("OPENAI_MAX_IMAGES_PER_REQUEST", "1"))
        self.max_images_per_chunk = max(1, int(configured_limit))

    def estimate(self, messages) -> int:
        if self.size_estimator:
            return self.size_estimator(messages)
        import json
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _messages_size(self, segments, image_urls, **kwargs) -> int:
        messages = self.message_builder(segments, image_urls, **kwargs)
        return self.estimate(messages)

    def _get_text(self, segment) -> str:
        if isinstance(segment, dict):
            return segment.get("text", "")
        return getattr(segment, "text", "")

    def _make_segment(self, segment, text: str, start=None, end=None):
        if isinstance(segment, dict):
            new_seg = dict(segment)
            new_seg["text"] = text
            if start is not None:
                new_seg["start"] = start
            if end is not None:
                new_seg["end"] = end
            return new_seg
        if hasattr(segment, "__dict__"):
            data = dict(segment.__dict__)
            data["text"] = text
            if start is not None:
                data["start"] = start
            if end is not None:
                data["end"] = end
            return type(segment)(**data)
        seg_start = segment.start if start is None else start
        seg_end = segment.end if end is None else end
        return type(segment)(seg_start, seg_end, text)

    @staticmethod
    def _segment_field(segment, name: str, default=0.0):
        if isinstance(segment, dict):
            return segment.get(name, default)
        return getattr(segment, name, default)

    def _redistribute_segments(self, chunks, target_count: int, sample_image, **kwargs):
        """把已有正文重新分配到 target_count 个 chunk，保证每个图片 chunk 都带文本。

        原实现在 max_images_per_chunk 触顶后会新建 segments 为空的 chunk，
        模型只能看到图片、看不到转写正文，从而凭空编造内容。
        """
        segments = [seg for chunk in chunks for seg in chunk.segments]
        if not segments:
            return [ChunkPayload(segments=[], image_urls=[]) for _ in range(target_count)]

        # 完整正文 + 单张图仍能装进一次请求时，让每个 chunk 都携带完整正文，
        # 模型因此始终拥有全局上下文；否则按时间比例切片，避免请求体积失控。
        if self._messages_size(segments, [sample_image], **kwargs) <= self.max_bytes:
            return [ChunkPayload(segments=list(segments), image_urls=[]) for _ in range(target_count)]

        full_text = "".join(self._get_text(seg) for seg in segments)
        if not full_text:
            return [ChunkPayload(segments=[], image_urls=[]) for _ in range(target_count)]

        template = segments[0]
        span_start = self._segment_field(segments[0], "start")
        span_end = self._segment_field(segments[-1], "end")
        span = max(float(span_end) - float(span_start), 0.0)
        total_chars = len(full_text)

        result = []
        for idx in range(target_count):
            char_start = total_chars * idx // target_count
            char_end = total_chars * (idx + 1) // target_count
            piece = full_text[char_start:char_end]
            if not piece:
                result.append(ChunkPayload(segments=[], image_urls=[]))
                continue
            seg_start = float(span_start) + span * idx / target_count
            seg_end = float(span_start) + span * (idx + 1) / target_count
            result.append(
                ChunkPayload(
                    segments=[self._make_segment(template, piece, start=seg_start, end=seg_end)],
                    image_urls=[],
                )
            )
        return result

    def _carry_segments(self, chunks, image, **kwargs):
        """兜底：为新建的图片 chunk 选择正文，优先复用最近的完整正文。"""
        for chunk in reversed(chunks):
            if not chunk.segments:
                continue
            if self._messages_size(chunk.segments, [image], **kwargs) <= self.max_bytes:
                return list(chunk.segments)
            full_text = "".join(self._get_text(seg) for seg in chunk.segments)
            if full_text:
                return [self._make_segment(chunk.segments[0], full_text[: max(1, len(full_text) // 4)])]
        return []

    def _split_segment_to_fit(self, segment, **kwargs):
        text = self._get_text(segment)
        if not text:
            raise ValueError("empty segment cannot be split")
        lo, hi = 1, len(text)
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = self._make_segment(segment, text[:mid])
            size = self._messages_size([candidate], [], **kwargs)
            if size <= self.max_bytes:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            raise ValueError("single segment too large to fit request")
        head = self._make_segment(segment, text[:best])
        tail = self._make_segment(segment, text[best:])
        return head, tail

    def chunk(self, segments: list, image_urls: list, **kwargs) -> List[ChunkPayload]:
        segments = list(segments or [])
        image_urls = list(image_urls or [])
        if not segments and not image_urls:
            return []

        chunks: List[ChunkPayload] = []
        seg_idx = 0

        while seg_idx < len(segments):
            batch_segments = []
            while seg_idx < len(segments):
                candidate = batch_segments + [segments[seg_idx]]
                size = self._messages_size(candidate, [], **kwargs)
                if size <= self.max_bytes:
                    batch_segments = candidate
                    seg_idx += 1
                    continue
                if not batch_segments:
                    head, tail = self._split_segment_to_fit(segments[seg_idx], **kwargs)
                    segments[seg_idx] = head
                    segments.insert(seg_idx + 1, tail)
                    continue
                break

            if not batch_segments:
                raise ValueError("unable to fit any content into chunk")

            chunks.append(ChunkPayload(segments=batch_segments, image_urls=[]))

        if not image_urls:
            return chunks

        if not chunks:
            chunks = [ChunkPayload(segments=[], image_urls=[])]

        if not segments:
            for image in image_urls:
                appended = False
                for chunk in chunks[-1:]:
                    candidate_images = chunk.image_urls + [image]
                    if (
                        len(candidate_images) <= self.max_images_per_chunk
                        and self._messages_size(chunk.segments, candidate_images, **kwargs) <= self.max_bytes
                    ):
                        chunk.image_urls = candidate_images
                        appended = True
                        break

                if appended:
                    continue

                if self._messages_size([], [image], **kwargs) > self.max_bytes:
                    raise ValueError("single image payload exceeds max_bytes")
                chunks.append(ChunkPayload(segments=[], image_urls=[image]))
            return chunks

        chunk_count = len(chunks)
        total_images = len(image_urls)

        # 图片所需 chunk 数超过现有数量时，先把正文重新铺开，
        # 否则容量触顶后每张图都会落到一个没有任何转写文本的新 chunk 上。
        needed_chunks = (total_images + self.max_images_per_chunk - 1) // self.max_images_per_chunk
        if needed_chunks > chunk_count:
            chunks = self._redistribute_segments(chunks, needed_chunks, image_urls[0], **kwargs)
            chunk_count = len(chunks)

        for idx, image in enumerate(image_urls):
            preferred_idx = min(chunk_count - 1, (idx * chunk_count) // total_images)
            placed = False

            for chunk_idx in range(preferred_idx, len(chunks)):
                chunk = chunks[chunk_idx]
                candidate_images = chunk.image_urls + [image]
                if (
                    len(candidate_images) <= self.max_images_per_chunk
                    and self._messages_size(chunk.segments, candidate_images, **kwargs) <= self.max_bytes
                ):
                    chunk.image_urls = candidate_images
                    placed = True
                    break

            if placed:
                continue

            if self._messages_size([], [image], **kwargs) > self.max_bytes:
                raise ValueError("single image payload exceeds max_bytes")
            chunks.append(ChunkPayload(segments=self._carry_segments(chunks, image, **kwargs), image_urls=[image]))

        return chunks

    def group_texts_by_budget(self, texts: List[str], build_messages: Callable, **kwargs) -> List[List[str]]:
        groups: List[List[str]] = []
        idx = 0
        while idx < len(texts):
            group: List[str] = []
            while idx < len(texts):
                candidate = group + [texts[idx]]
                try:
                    messages = build_messages(candidate, [], **kwargs)
                except TypeError:
                    messages = build_messages(candidate, **kwargs)
                size = self.estimate(messages)
                if size <= self.max_bytes:
                    group = candidate
                    idx += 1
                    continue
                if not group:
                    raise ValueError("single text block exceeds max_bytes")
                break
            groups.append(group)
        return groups
