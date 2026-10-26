import json
import os
import time
from typing import Optional

from app.gpt.gpt_factory import GPTFactory
from app.models.model_config import ModelConfig
from app.services.provider import ProviderService
from app.services.vector_store import VectorStoreManager
from app.services.chat_tools import TOOLS, execute_tool
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 单次 chat/completions 的 HTTP 超时（秒）。前端 axios 是 60s，这里取 45s，
# 让后端先超时并返回结构化错误，而不是前端先断开、后端继续烧 token。
CHAT_REQUEST_TIMEOUT = float(os.getenv("CHAT_REQUEST_TIMEOUT_SECONDS", "45"))
# 整条 `/chat/ask` 的时间预算（秒）。最多 3 轮工具调用 + 1 次兜底 = 4 次
# create，所以这里按「单次超时 × 1.5」左右给，避免 4 次全用满。
CHAT_TOTAL_TIMEOUT = float(os.getenv("CHAT_TOTAL_TIMEOUT_SECONDS", "90"))
# 单次问答最多触发几次工具调用。3 轮 × 每次 1 个工具，足够覆盖
# 「先查元信息 → 再查原文 → 最后作答」的典型路径。
CHAT_MAX_TOOL_ROUNDS = int(os.getenv("CHAT_MAX_TOOL_ROUNDS", "3"))

SYSTEM_PROMPT = """你是一个视频笔记问答助手。你拥有以下能力：

1. 系统已自动检索了一些相关内容作为初始参考（见下方）
2. 你可以调用工具主动查询更多信息：
   - lookup_transcript: 查询视频原始转录文本（支持按时间、关键词、位置筛选）
   - get_video_info: 获取视频元信息（标题、作者、简介、标签等）
   - get_note_content: 获取完整笔记内容

--- 初始检索内容 ---
{context}
---

回答要求：
- 如果初始检索内容不足以回答问题，请主动调用工具获取更多信息
- 回答关于视频具体原话、细节时，用 lookup_transcript 查询原文
- 回答关于作者、标题等基本信息时，用 get_video_info 查询
- 请用中文回答，保持简洁准确"""


def _build_context(chunks: list[dict]) -> str:
    """将检索到的片段拼接为上下文文本。"""
    parts = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        source_type = meta.get("source_type", "unknown")
        if source_type == "meta":
            label = "[视频信息]"
        elif source_type == "markdown":
            label = f"[笔记 - {meta.get('section_title', '')}]"
        else:
            start = meta.get("start_time", 0)
            end = meta.get("end_time", 0)
            label = f"[转录 - {start:.0f}s~{end:.0f}s]"
        parts.append(f"{label}\n{chunk['text']}")
    return "\n\n".join(parts)


def _build_sources(chunks: list[dict]) -> list[dict]:
    """从检索片段中提取来源信息。"""
    sources = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        source = {
            "text": chunk["text"][:200],
            "source_type": meta.get("source_type", "unknown"),
        }
        if meta.get("section_title"):
            source["section_title"] = meta["section_title"]
        if meta.get("start_time") is not None:
            source["start_time"] = meta["start_time"]
        if meta.get("end_time") is not None:
            source["end_time"] = meta["end_time"]
        sources.append(source)
    return sources


def _sanitize_history(history: list[dict]) -> list[dict]:
    """把客户端传来的历史消息收敛成「最多 20 条、只含 user/assistant、按轮成对」。

    ## 三个历史包袱，一次收敛掉

    1. **role 注入**：`/chat/ask` 的 `ChatMessage.role` 已改成枚举，但这里是
       第二道防线 —— 内部调用方/未来新增端点也可能传裸 dict。
    2. **重复当前问题**：前端在请求失败后会留下「没有回答的 user 消息」，
       用户重问同一句时 history 里就带着它，后端再 append 一次 → 同一句
       question 在上下文里出现两遍，模型容易自我复述。
    3. **按条截断会切出孤立 assistant**：`history[-20:]` 可能从一条 assistant
       开始，导致「助手先说话」的异常序列；按轮对齐更稳。

    只保留最后 20 条，且若截断后首条是 assistant 则丢弃它（保证从 user 开始）。
    """
    cleaned: list[dict] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        cleaned.append({"role": role, "content": content})

    trimmed = cleaned[-20:]
    # 截断后若以 assistant 开头，丢掉它 —— 对话必须从 user 开始
    while trimmed and trimmed[0]["role"] == "assistant":
        trimmed.pop(0)
    return trimmed


def chat(
    task_id: str,
    question: str,
    history: list[dict],
    provider_id: str,
    model_name: str,
    user_id: int | None = None,
) -> dict:
    """
    RAG + Tool Calling 问答。
    1. 向量检索初始上下文
    2. 调用 LLM（带 tools）
    3. 如果 LLM 调用了工具，执行工具并将结果返回给 LLM
    4. 循环直到 LLM 给出最终回答
    """
    vector_store = VectorStoreManager()

    # 1. 检索初始上下文
    chunks = vector_store.query(task_id, question, n_results=6)
    context = _build_context(chunks) if chunks else "（未检索到相关内容，请使用工具查询）"
    sources = _build_sources(chunks) if chunks else []

    # 2. 构建消息
    system_msg = SYSTEM_PROMPT.format(context=context)
    messages = [{"role": "system", "content": system_msg}]

    messages.extend(_sanitize_history(history))

    messages.append({"role": "user", "content": question})

    # 3. 获取 LLM client
    #
    # BYOK：统一走 credential_resolver（唯一「用谁的 key」入口）。
    # ``provider_id`` 形如 ``own:llm`` 时表示用当前用户自带凭据。
    from app.services.credential_resolver import resolve as resolve_credential

    capability = "llm"
    site_provider_id = provider_id
    if provider_id and provider_id.startswith("own:"):
        capability = provider_id.split(":", 1)[1] or "default"
        site_provider_id = None

    cred = resolve_credential(
        user_id,
        capability,
        model_name=model_name,
        site_provider_id=site_provider_id,
    )

    config = ModelConfig(
        api_key=cred.api_key,
        base_url=cred.base_url,
        model_name=cred.model_name or model_name,
        provider=cred.source,
        name=cred.source,
    )
    # 显式短超时 + 关闭 SDK 自动重试：这是「用户在前台等」的接口，
    # 不能用 600s × 3 次的默认策略（最坏 2 小时，且会占满线程池）。
    gpt = GPTFactory.from_config(
        config, timeout=CHAT_REQUEST_TIMEOUT, max_retries=0,
    )

    logger.info(
        f"Chat: task_id={task_id}, model={cred.model_name or model_name}, "
        f"source={cred.source}"
    )

    deadline = time.monotonic() + CHAT_TOTAL_TIMEOUT

    def _remaining() -> float:
        return max(1.0, deadline - time.monotonic())

    def _create(**extra):
        """带剩余预算的单次 create。"""
        return gpt.client.chat.completions.create(
            model=gpt.model,
            messages=messages,
            temperature=0.7,
            timeout=_remaining(),
            **extra,
        )

    # 4. Tool calling 循环
    for round_i in range(CHAT_MAX_TOOL_ROUNDS):
        response = _create(tools=TOOLS)

        msg = response.choices[0].message

        # 没有工具调用，直接返回
        if not msg.tool_calls:
            return {"answer": msg.content or "", "sources": sources}

        # 处理工具调用
        messages.append(msg)

        for tool_call in msg.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info(f"Tool call [{round_i+1}/{CHAT_MAX_TOOL_ROUNDS}]: {fn_name}({fn_args})")

            result = execute_tool(task_id, fn_name, fn_args)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # 超过最大轮次，做最后一次不带 tools 的调用。
    # 注意仍带着前面几轮的 assistant.tool_calls + tool 结果 —— 部分兼容端点
    # 会因此报 400（消息序列「有 tool 结果但没有 tools 声明」）。这类失败不该
    # 变成 500，降级为「把已拿到的信息拼成答案」的兜底。
    try:
        response = _create()
    except Exception as exc:  # noqa: BLE001 - 兜底失败时返回结构化错误
        logger.warning(f"工具轮次耗尽后的收尾调用失败，返回降级结果: {exc}")
        return {
            "answer": "（这轮对话检索了较多原文，已达工具调用上限但未能生成最终回答，"
                      "请把问题问得更具体一些再试。）",
            "sources": sources,
        }

    return {"answer": response.choices[0].message.content or "", "sources": sources}
