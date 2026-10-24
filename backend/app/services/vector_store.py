import json
import os
import re
from typing import Optional

import chromadb
from chromadb.config import Settings

from app.utils.logger import get_logger
from app.utils.note_paths import UnsafeTaskId, note_file

logger = get_logger(__name__)

NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
VECTOR_DB_DIR = os.getenv("VECTOR_DB_DIR", "vector_db")


def _build_embedding_function():
    """通过 OpenAI 兼容端点做向量化（本地 TEI / 统一网关）。

    未配置 EMBEDDING_API_BASE 时返回 None，沿用 chromadb 内置模型
    （首次使用会下载 ONNX 权重，离线环境容易卡住）。
    """
    api_base = os.getenv("EMBEDDING_API_BASE", "").strip()
    if not api_base:
        return None
    try:
        from chromadb.utils import embedding_functions

        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.getenv("EMBEDDING_API_KEY", "none"),
            api_base=api_base,
            model_name=os.getenv("EMBEDDING_MODEL", "Qwen3-Embedding-0.6B"),
        )
    except Exception as exc:
        logger.warning(f"外部 embedding 初始化失败，回退内置模型: {exc}")
        return None


def _chunk_markdown(markdown: str) -> list[dict]:
    """按 H2/H3 标题拆分 markdown 为语义块。"""
    sections = re.split(r'(?=^#{2,3}\s)', markdown, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section or len(section) < 30:
            continue
        heading_match = re.match(r'^(#{2,3})\s+(.+)', section)
        title = heading_match.group(2).strip() if heading_match else "intro"
        chunks.append({
            "text": section,
            "metadata": {"source_type": "markdown", "section_title": title},
        })
    return chunks


def _chunk_transcript(segments: list[dict], window_size: int = 15, overlap: int = 3) -> list[dict]:
    """将转录 segments 按滑动窗口分组。"""
    if not segments:
        return []
    chunks = []
    step = max(window_size - overlap, 1)
    for i in range(0, len(segments), step):
        window = segments[i:i + window_size]
        if not window:
            break
        text = "\n".join(
            f"[{seg.get('start', 0):.0f}s] {seg.get('text', '')}" for seg in window
        )
        chunks.append({
            "text": text,
            "metadata": {
                "source_type": "transcript",
                "start_time": window[0].get("start", 0),
                "end_time": window[-1].get("end", 0),
            },
        })
    return chunks


def _build_meta_chunk(audio_meta: dict) -> list[dict]:
    """将视频元信息（标题、作者、描述、标签等）构建为可检索的 chunk。"""
    if not audio_meta:
        return []

    raw = audio_meta.get("raw_info", {}) or {}
    parts = []

    title = audio_meta.get("title") or raw.get("title", "")
    if title:
        parts.append(f"视频标题：{title}")

    uploader = raw.get("uploader", "")
    if uploader:
        parts.append(f"视频作者/UP主：{uploader}")

    desc = raw.get("description", "")
    if desc:
        parts.append(f"视频简介：{desc[:500]}")

    tags = raw.get("tags", [])
    if tags and isinstance(tags, list):
        parts.append(f"标签：{', '.join(str(t) for t in tags[:20])}")

    duration = audio_meta.get("duration", 0)
    if duration:
        m, s = divmod(int(duration), 60)
        parts.append(f"视频时长：{m}分{s}秒")

    platform = audio_meta.get("platform", "")
    if platform:
        parts.append(f"平台：{platform}")

    url = raw.get("webpage_url", "")
    if url:
        parts.append(f"链接：{url}")

    if not parts:
        return []

    return [{
        "text": "\n".join(parts),
        "metadata": {"source_type": "meta"},
    }]


class VectorStoreManager:
    """基于 ChromaDB 的笔记向量存储管理器。"""

    def __init__(self):
        os.makedirs(VECTOR_DB_DIR, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=VECTOR_DB_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        self._embedding_fn = _build_embedding_function()
        # 外部 embedding 与内置模型维度不同（如 1024 vs 384），
        # 用后缀隔离索引，避免新旧 collection 混用导致查询维度冲突。
        default_suffix = "_ext" if self._embedding_fn is not None else ""
        self._suffix = os.getenv("VECTOR_COLLECTION_SUFFIX", default_suffix)

    def _collection_name(self, task_id: str) -> str:
        """ChromaDB collection 名称：task_id + 可选后缀。"""
        return f"{task_id}{self._suffix}" if self._suffix else task_id

    def _create_collection(self, name: str):
        kwargs = {"name": name, "metadata": {"hnsw:space": "cosine"}}
        if self._embedding_fn is not None:
            kwargs["embedding_function"] = self._embedding_fn
        return self._client.create_collection(**kwargs)

    def _get_collection(self, name: str):
        if self._embedding_fn is not None:
            return self._client.get_collection(name, embedding_function=self._embedding_fn)
        return self._client.get_collection(name)

    def index_task(self, task_id: str) -> bool:
        """读取笔记结果并建立向量索引。

        返回 **是否真的建好了索引**。历史实现返回 None，且「笔记文件不存在」
        与「内容为空」都静默 return —— 调用方（`_do_index`）只能靠「没抛异常」
        猜成功，于是把「什么都没索引」报成 `indexed`，用户进到问答界面后每问
        都是空上下文。

        task_id 走 `note_file()` 沙箱：越界值直接判失败，不读目录外文件。
        """
        try:
            result_path = note_file(NOTE_OUTPUT_DIR, str(task_id), ".json")
        except UnsafeTaskId:
            logger.error(f"拒绝索引越界 task_id: {task_id!r}")
            return False

        if not os.path.exists(result_path):
            logger.warning(f"笔记文件不存在，跳过索引: {result_path}")
            return False

        with open(result_path, "r", encoding="utf-8") as f:
            note_data = json.load(f)

        # markdown 可能是 null/非字符串（历史数据、异常写入），统一降级成空串，
        # 否则 _chunk_markdown 里的 re.split 会抛 TypeError。
        markdown = note_data.get("markdown") or ""
        if not isinstance(markdown, str):
            markdown = ""
        transcript = note_data.get("transcript") or {}
        segments = transcript.get("segments") or []

        audio_meta = note_data.get("audio_meta") or {}

        meta_chunks = _build_meta_chunk(audio_meta)
        md_chunks = _chunk_markdown(markdown)
        tr_chunks = _chunk_transcript(segments)
        all_chunks = meta_chunks + md_chunks + tr_chunks

        if not all_chunks:
            logger.warning(f"笔记内容为空，无法建立索引: {task_id}")
            return False

        col_name = self._collection_name(task_id)

        # 删除旧 collection（幂等）
        try:
            self._client.delete_collection(col_name)
        except Exception:
            pass

        collection = self._create_collection(col_name)

        documents = [c["text"] for c in all_chunks]
        metadatas = [c["metadata"] for c in all_chunks]
        ids = [f"{task_id}_{i}" for i in range(len(all_chunks))]

        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        logger.info(f"向量索引完成: task_id={task_id}, chunks={len(all_chunks)}")
        return True

    def _parse_results(self, results: dict) -> list[dict]:
        """将 ChromaDB query 结果转换为 chunk 列表。"""
        chunks = []
        if not results or not results.get("documents") or not results["documents"][0]:
            return chunks
        for i in range(len(results["documents"][0])):
            chunks.append({
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else None,
            })
        return chunks

    def query(self, task_id: str, query_text: str, n_results: int = 6) -> list[dict]:
        """
        按固定配额从各来源检索：meta 1 条、markdown 2 条、transcript 3 条，
        确保三种来源都被召回。
        """
        col_name = self._collection_name(task_id)
        try:
            collection = self._get_collection(col_name)
        except Exception:
            logger.warning(f"Collection 不存在: {col_name}")
            return []

        all_chunks = []

        # 每种来源的配额
        quotas = {"meta": 1, "markdown": 2, "transcript": 3}

        for source_type, quota in quotas.items():
            try:
                results = collection.query(
                    query_texts=[query_text],
                    n_results=quota,
                    where={"source_type": source_type},
                )
                all_chunks.extend(self._parse_results(results))
            except Exception:
                pass

        return all_chunks

    def delete_index(self, task_id: str) -> None:
        """删除指定任务的向量索引。"""
        col_name = self._collection_name(task_id)
        try:
            self._client.delete_collection(col_name)
            logger.info(f"已删除向量索引: {task_id}")
        except Exception:
            pass

    def is_indexed(self, task_id: str) -> bool:
        """指定任务是否已建立可用索引。

        ## 为什么不要求必须有 meta chunk（历史行为改掉了）

        旧实现要求 collection 里存在 `source_type=meta` 的 chunk，否则判 False。
        但 meta chunk 依赖 `audio_meta` 有内容（本地视频、旧数据、部分平台都可能
        拿不到），于是这类笔记**永远** is_indexed=False —— 前端 `/chat/status`
        每次拿到 idle 就重新触发一次全量索引，形成无意义的重建循环。

        检索本身并不依赖 meta 存在（`query()` 按来源分别取，缺哪个就少哪一项），
        所以判据改为「collection 存在且非空」。
        """
        col_name = self._collection_name(task_id)
        try:
            col = self._get_collection(col_name)
            return col.count() > 0
        except Exception:
            return False
