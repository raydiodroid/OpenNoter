import importlib.util
import pathlib
import unittest
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "gpt" / "request_chunker.py"
spec = importlib.util.spec_from_file_location("request_chunker", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("request_chunker module spec not found")
request_chunker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(request_chunker)
RequestChunker = request_chunker.RequestChunker


@dataclass
class DummySeg:
    start: float
    end: float
    text: str


def build_messages(segments, image_urls, **_):
    content = [{"type": "text", "text": "".join(s.text for s in segments)}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "auto"}})
    return [{"role": "user", "content": content}]


def size_estimator(messages):
    size = 0
    for part in messages[0]["content"]:
        if part["type"] == "text":
            size += len(part["text"])
        else:
            size += len(part["image_url"]["url"])
    return size


class TestRequestChunker(unittest.TestCase):
    def test_chunk_segments_preserves_order_and_content(self):
        segments = [
            DummySeg(0, 1, "aaaa"),
            DummySeg(1, 2, "bbbb"),
            DummySeg(2, 3, "cccc"),
        ]
        chunker = RequestChunker(build_messages, max_bytes=8, size_estimator=size_estimator)
        chunks = chunker.chunk(segments, [])
        texts = ["".join(seg.text for seg in c.segments) for c in chunks]
        self.assertEqual("".join(texts), "aaaabbbbcccc")
        self.assertTrue(all(texts))

    def test_chunk_images_distributed_across_batches(self):
        segments = [DummySeg(0, 1, "aa")]
        images = ["i" * 6, "j" * 6, "k" * 6]
        chunker = RequestChunker(build_messages, max_bytes=10, size_estimator=size_estimator)
        chunks = chunker.chunk(segments, images)
        all_images = [img for c in chunks for img in c.image_urls]
        self.assertEqual(all_images, images)

    def test_chunk_images_respect_explicit_per_request_limit(self):
        segments = [DummySeg(0, 1, "transcript")]
        images = ["image-a", "image-b", "image-c"]
        chunker = RequestChunker(
            build_messages,
            max_bytes=10**6,
            size_estimator=size_estimator,
            max_images_per_chunk=1,
        )
        chunks = chunker.chunk(segments, images)
        self.assertTrue(all(len(chunk.image_urls) <= 1 for chunk in chunks))
        self.assertEqual([image for chunk in chunks for image in chunk.image_urls], images)

    def test_chunk_images_are_not_front_loaded_when_multiple_segment_chunks(self):
        segments = [
            DummySeg(0, 1, "aaaaaa"),
            DummySeg(1, 2, "bbbbbb"),
            DummySeg(2, 3, "cccccc"),
        ]
        images = ["11111", "22222", "33333"]
        chunker = RequestChunker(build_messages, max_bytes=12, size_estimator=size_estimator)
        chunks = chunker.chunk(segments, images)

        self.assertGreaterEqual(len(chunks), 3)
        image_counts = [len(c.image_urls) for c in chunks]
        self.assertGreater(image_counts[1], 0)
        self.assertGreater(image_counts[2], 0)
        all_images = [img for c in chunks for img in c.image_urls]
        self.assertEqual(all_images, images)

    def test_split_oversized_segment(self):
        segments = [DummySeg(0, 1, "x" * 25)]
        chunker = RequestChunker(build_messages, max_bytes=10, size_estimator=size_estimator)
        chunks = chunker.chunk(segments, [])
        combined = "".join(seg.text for c in chunks for seg in c.segments)
        self.assertEqual(combined, "x" * 25)

    def test_group_texts_by_budget(self):
        chunker = RequestChunker(build_messages, max_bytes=10, size_estimator=size_estimator)

        def build_text_messages(texts, *_args, **_kwargs):
            content = [{"type": "text", "text": "".join(texts)}]
            return [{"role": "user", "content": content}]

        groups = chunker.group_texts_by_budget(["aaaaa", "bbbbb", "ccccc"], build_text_messages)
        self.assertEqual(groups, [["aaaaa", "bbbbb"], ["ccccc"]])

    def test_image_chunks_never_lose_transcript(self):
        """回归：图片数超过 chunk 容量时，每个图片 chunk 都必须带正文。

        原实现会在 max_images_per_chunk 触顶后追加 segments 为空的新 chunk，
        导致模型只看图不看转写，凭空编造内容。
        """
        segments = [DummySeg(0, 906, "转写正文" * 400)]
        images = ["img-%02d" % i for i in range(37)]
        chunker = RequestChunker(
            build_messages,
            max_bytes=10**6,
            size_estimator=size_estimator,
            max_images_per_chunk=1,
        )
        chunks = chunker.chunk(segments, images)

        self.assertEqual(len(chunks), 37)
        self.assertEqual([c.image_urls[0] for c in chunks], images)
        for idx, chunk in enumerate(chunks):
            self.assertEqual(len(chunk.image_urls), 1)
            text = "".join(seg.text for seg in chunk.segments)
            self.assertTrue(text, "第 %d 个图片 chunk 缺少正文" % idx)

    def test_full_transcript_is_carried_when_it_fits(self):
        """正文能装进一次请求时，每个图片 chunk 都携带完整正文。"""
        segments = [DummySeg(0, 600, "完整转写" * 100)]
        images = ["a", "b", "c"]
        chunker = RequestChunker(
            build_messages,
            max_bytes=10**6,
            size_estimator=size_estimator,
            max_images_per_chunk=1,
        )
        chunks = chunker.chunk(segments, images)
        expected = "完整转写" * 100
        for chunk in chunks:
            self.assertEqual("".join(seg.text for seg in chunk.segments), expected)

    def test_oversized_transcript_is_sliced_with_monotonic_timestamps(self):
        """正文装不下时按比例切片，且切片时间戳递增。"""
        segments = [DummySeg(0, 100, "x" * 5000)]
        images = ["a", "b", "c", "d"]
        chunker = RequestChunker(
            build_messages,
            max_bytes=2000,
            size_estimator=size_estimator,
            max_images_per_chunk=1,
        )
        chunks = chunker.chunk(segments, images)

        image_chunks = [c for c in chunks if c.image_urls]
        self.assertEqual(len(image_chunks), 4)
        for chunk in image_chunks:
            self.assertTrue(chunk.segments, "切片后的图片 chunk 不应缺少正文")
            self.assertLessEqual(
                len("".join(seg.text for seg in chunk.segments)) + len(chunk.image_urls[0]),
                2000,
            )

        starts = [c.segments[0].start for c in image_chunks]
        self.assertEqual(starts, sorted(starts))
        self.assertGreater(starts[-1], starts[0])


if __name__ == "__main__":
    unittest.main()
