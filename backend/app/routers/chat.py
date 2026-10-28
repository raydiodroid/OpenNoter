import re
from typing import Optional
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db.models.users import User
from app.services.auth import get_optional_user
from app.services.chat_service import chat as chat_service
from app.services.vector_store import VectorStoreManager
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(get_optional_user)])

# 索引状态追踪: task_id -> "indexing" | "indexed" | "failed"
_index_status: dict[str, str] = {}

def _require_task_access(task_id: str, user: User | None) -> None:
    """Chat/Index 与笔记接口共用任务 ACL，防止凭 task_id 对他人笔记做 RAG。"""
    from app.routers.note import _can_access_task

    if not _can_access_task(task_id, user):
        raise HTTPException(status_code=403, detail="无权访问该任务")




class IndexRequest(BaseModel):
    task_id: str


class ChatMessage(BaseModel):
    """历史消息。

    `role` 必须是枚举而不是裸 str：`/chat/ask` 会把 history 原样拼进下发给
    LLM 的 messages，若允许任意字符串，任何调用者都能塞一条
    `{"role": "system", "content": "忽略之前的指令…"}`，且它排在业务
    SYSTEM_PROMPT **之后**（多数实现里后置指令更容易覆盖前置）。这是提示词
    注入面，虽是「用自己的 token 越自己的狱」，但没有理由保留这个自由度。

    只放行前端真实会传的两种角色；`tool` 轮由服务端自己生成，不需要客户端传。
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=32_000)


class AskRequest(BaseModel):
    task_id: str
    question: str
    history: list[ChatMessage] = []

    # provider_id / model_name 改为可选：
    #
    # 历史原因，前端把「这个任务当时用的模型」存在 localStorage 的 formData 里。
    # 换设备 / 清缓存后从服务端同步回来的占位任务没有这两个字段，于是聊天会
    # 直接拒绝发送（"这条笔记缺少模型信息"）。而 BYOK 之后，很多任务本来
    # 就该由「当前用户自己的凭据」来回答，不需要任务里再记一份。
    #
    # 缺省时由 credential_resolver 解析：
    #   - BYOK 用户 → 用他自己的默认凭据
    #   - 站点模式  → 用站点默认供应商/模型
    provider_id: Optional[str] = None
    model_name: Optional[str] = None


def _do_index(task_id: str):
    """后台执行索引任务。

    `index_task()` 现在返回**是否真的建好**，所以这里不能再把「无异常」当成功：
    笔记文件缺失、笔记内容为空都会返回 False，必须如实置 `failed`，否则前端
    会显示「已就绪」并让用户对空上下文提问（历史假阳性 bug）。
    """
    try:
        _index_status[task_id] = "indexing"
        store = VectorStoreManager()
        ok = store.index_task(task_id)
        if ok:
            _index_status[task_id] = "indexed"
            logger.info(f"索引完成: {task_id}")
        else:
            _index_status[task_id] = "failed"
            logger.error(f"索引未建立（笔记缺失或内容为空）: {task_id}")
    except Exception as e:
        _index_status[task_id] = "failed"
        logger.error(f"索引失败: {task_id}, {e}")


@router.post("/chat/index")
def index_task(data: IndexRequest, background_tasks: BackgroundTasks,
               user: User | None = Depends(get_optional_user)):
    """触发后台索引，立即返回。"""
    _require_task_access(data.task_id, user)
    if _index_status.get(data.task_id) == "indexing":
        return R.success(msg="正在索引中")

    # 如果已经索引过，直接返回
    store = VectorStoreManager()
    if store.is_indexed(data.task_id):
        _index_status[data.task_id] = "indexed"
        return R.success(msg="已完成索引")

    _index_status[data.task_id] = "indexing"
    background_tasks.add_task(_do_index, data.task_id)
    return R.success(msg="开始索引")


@router.get("/chat/status")
def chat_status(task_id: str, user: User | None = Depends(get_optional_user)):
    """返回索引状态：idle / indexing / indexed / failed。"""
    _require_task_access(task_id, user)
    try:
        # 优先检查内存状态
        status = _index_status.get(task_id)
        if status:
            return R.success(data={"status": status, "indexed": status == "indexed"})

        # 内存没有记录，检查持久化
        store = VectorStoreManager()
        indexed = store.is_indexed(task_id)
        if indexed:
            _index_status[task_id] = "indexed"
        return R.success(data={"status": "indexed" if indexed else "idle", "indexed": indexed})
    except Exception as e:
        logger.error(f"查询索引状态失败: {e}")
        return R.success(data={"status": "idle", "indexed": False})


@router.post("/chat/ask")
def ask_question(data: AskRequest, user: User | None = Depends(get_optional_user)):
    """基于笔记内容的 RAG 问答。"""
    _require_task_access(data.task_id, user)
    try:
        history = [{"role": m.role, "content": m.content} for m in data.history]
        result = chat_service(
            task_id=data.task_id,
            question=data.question,
            history=history,
            provider_id=data.provider_id,
            model_name=data.model_name,
            # BYOK：带上请求者身份，用于解析「用谁的凭据」
            user_id=getattr(user, "id", None),
        )
        return R.success(data=result)
    except ValueError as e:
        # 配置类错误（供应商不存在 / key 未配置）本身就是为了给用户看的
        return R.error(msg=_safe_error_message(e), code=400)
    except Exception as e:  # noqa: BLE001
        # BYOK 的两类专用异常带的是给用户看的文案，不该被当成服务端错误吞掉
        from app.services.credential_resolver import (
            CredentialBroken,
            CredentialRequired,
        )

        if isinstance(e, (CredentialRequired, CredentialBroken)):
            return R.error(msg=str(e), code=400)
        raise
    except Exception as e:
        logger.error(f"Chat 问答失败: {e}", exc_info=True)
        return R.error(msg=_safe_error_message(e))


def _safe_error_message(exc: Exception) -> str:
    """把上游异常转成可以给终端用户看的文案。

    ## 为什么不能直接 `f"问答失败: {str(e)}"`

    上游 SDK 的异常文本里经常带凭据与内网地址，实测：
      - `AuthenticationError`: `Incorrect API key provided: sk-live-***...5b4a`
      - `NotFoundError`: `The model `x` does not exist at https://internal-gw/v1`

    直接拼接等于把 API Key 片段和内部网关地址回给前端（也会进浏览器控制台/
    错误上报）。这里做两层处理：

    1. 按异常类型给出可行动的预置文案（用户能据此自助修复）；
    2. 对兜底文案做正则脱敏，去掉 `sk-*`、URL、Bearer 头等敏感片段。
    """
    try:
        import openai  # 延迟 import：chat 模块在无 openai 的环境也要能加载

        if isinstance(exc, openai.AuthenticationError):
            return "模型供应商鉴权失败：API Key 无效或已过期，请到「设置」里重新填写"
        if isinstance(exc, openai.PermissionDeniedError):
            return "模型供应商拒绝访问：请确认该 API Key 有调用此模型的权限"
        if isinstance(exc, openai.NotFoundError):
            return "模型或接口不存在：请确认模型名称与供应商 Base URL 配置正确"
        if isinstance(exc, openai.RateLimitError):
            return "模型供应商限流或额度用尽，请稍后再试"
        if isinstance(exc, openai.APITimeoutError):
            return "模型供应商响应超时，请稍后再试"
        if isinstance(exc, openai.APIConnectionError):
            return "无法连接到模型供应商，请检查网络/代理或 Base URL 配置"
        if isinstance(exc, openai.BadRequestError):
            # 400 常是「模型不支持 tools」这类参数问题，原文对排查有价值
            return f"模型供应商拒绝了请求：{_redact(str(exc))[:300]}"
    except Exception:  # noqa: BLE001 - 脱敏逻辑本身绝不能再抛
        pass

    return f"问答失败：{_redact(str(exc))[:300]}"


# 需要从错误文本里抹掉的片段：明文 key、URL、Bearer 头
_SECRET_PATTERNS = (
    # sk-xxx / sk-proj-xxx / 其它常见前缀
    (re.compile(r"\b(sk|pk|rk|api)[-_][A-Za-z0-9_\-]{8,}"), r"\1-***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE), r"\1***"),
    # 内网/外部 URL（保留 scheme，隐去具体地址）
    (re.compile(r"https?://[^\s'\"\)]+"), "<上游地址已隐藏>"),
    # 长十六进制/base64 串（可能是密钥）
    (re.compile(r"\b[A-Za-z0-9]{32,}\b"), "***"),
)


def _redact(text: str) -> str:
    """抹掉错误文本里的凭据与地址片段。"""
    out = text
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out
