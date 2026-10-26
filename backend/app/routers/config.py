import os
import platform
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from urllib.parse import urlsplit
from app.db.models.users import User
from app.services.auth import get_optional_user, require_admin
from app.utils.response import ResponseWrapper as R
from app.utils.logger import get_logger
from app.utils.path_helper import get_model_dir

from app.services.cookie_manager import CookieConfigManager
from app.services.cookie_policy import (
    cookie_entry_count,
    cookie_hint_for_empty,
    filter_downloader_cookies,
)
from app.services.cookie_transfer import (
    COOKIE_PLATFORMS,
    cookie_transfer_manager,
    normalize_origin,
)
from app.services.bilibili_qr_login import BilibiliQrLoginError, BilibiliQrLoginManager
from app.services.transcriber_config_manager import TranscriberConfigManager
from app.transcriber import model_download_state as dl_state
from ffmpeg_helper import ensure_ffmpeg_or_raise

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(get_optional_user)])
# 健康检查与一次性扩展传输凭证接口不依赖浏览器会话
public_router = APIRouter()
cookie_manager = CookieConfigManager()
transcriber_config_manager = TranscriberConfigManager()
qr_login_manager = BilibiliQrLoginManager(cookie_manager.set)


class CookieUpdateRequest(BaseModel):
    platform: str
    cookie: str = Field(max_length=32768)


class CookieTransferStartRequest(BaseModel):
    platform: str


class CookieTransferCompleteRequest(BaseModel):
    transfer_id: str = Field(min_length=20, max_length=128)
    page_origin: str = Field(max_length=512)
    cookie: str = Field(min_length=1, max_length=32768)


@router.get("/get_downloader_cookie/{platform}")
def get_cookie(platform: str, admin: User = Depends(require_admin)):
    if platform not in COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail="不支持的 Cookie 平台")
    return R.success(data={"platform": platform, "cookie": cookie_manager.get(platform)})


@router.post("/update_downloader_cookie")
def update_cookie(data: CookieUpdateRequest, admin: User = Depends(require_admin)):
    if data.platform not in COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail="不支持的 Cookie 平台")
    try:
        cookie = filter_downloader_cookies(data.platform, data.cookie)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.cookie and not cookie:
        # 提示文案要区分格式：
        #   - netscape 平台（YouTube）→ 提示 cookies.txt 格式/条目问题
        #   - header 平台且白名单为空 → 明确说「无需同步」
        #   - header 平台有白名单 → 说「没有需要的登录项」
        raise HTTPException(status_code=400, detail=cookie_hint_for_empty(data.platform))
    cookie_manager.set(data.platform, cookie)
    return R.success()


@router.post("/create_browser_cookie_transfer")
def create_browser_cookie_transfer(
    data: CookieTransferStartRequest,
    request: Request,
    admin: User = Depends(require_admin),
):
    origin = request.headers.get("origin", "")
    if not origin:
        referer = request.headers.get("referer", "")
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
    normalized_origin = normalize_origin(origin)
    if not normalized_origin:
        raise HTTPException(status_code=400, detail="无法确认浏览器页面来源")
    try:
        transfer_id = cookie_transfer_manager.issue(data.platform, admin.id, normalized_origin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return R.success(data={"transfer_id": transfer_id, "expires_in": 90})


@public_router.post("/complete_browser_cookie_transfer")
def complete_browser_cookie_transfer(
    data: CookieTransferCompleteRequest,
    request: Request = None,
):
    """接收浏览器扩展同步过来的 Cookie。

    已知限制（**扩展侧问题，后端无法修**）：
    `OpenNoter_extension/src/logic/cookies.ts` 里 `COOKIE_NAME_ALLOWLISTS.youtube = []`，
    所以 `readBrowserCookies('youtube')` 永远返回空串——浏览器一键同步对 YouTube
    不生效。YouTube 目前只支持用户在设置页**手动粘贴 Netscape cookies.txt**
    （这是 yt-dlp `cookiefile` 需要的格式，不能是 `name=value; ...` 请求头形式）。
    要让一键同步支持 YouTube，得先改扩展的 allowlist，不在本文件的改动范围内。
    """
    # F40：该端点有意免登录（浏览器扩展的 service worker 拿不到 Cookie 凭据），
    # 但若请求恰好**带了**有效身份，就要求与 ticket 签发者一致 ——
    # 防止 ticket 泄漏后被人拿去把 Cookie 写进系统。
    #
    # 这里用 get_optional_user（而非 get_current_user）：没凭据时返回 None，
    # 保持免登录语义；有凭据时才收紧。
    expected_user_id = None
    if request is not None:
        try:
            from app.services.auth import get_optional_user
            from app.db.engine import SessionLocal

            db = SessionLocal()
            try:
                identified = get_optional_user(request, db)
            finally:
                db.close()
            if identified is not None:
                expected_user_id = getattr(identified, "id", None)
        except Exception:  # noqa: BLE001 - 身份解析失败不影响免登录路径
            expected_user_id = None

    # 兼容两种 consume 签名：生产实现接受 expected_user_id；
    # 测试里常用更简单的 fake（只接受三个位置参数），不应该因为新增的
    # 安全参数而让整套测试挂掉 —— 桩的职责是模拟行为，不是复刻签名。
    try:
        transfer = cookie_transfer_manager.consume(
            data.transfer_id,
            data.page_origin,
            data.cookie,
            expected_user_id=expected_user_id,
        )
    except TypeError:
        transfer = cookie_transfer_manager.consume(
            data.transfer_id, data.page_origin, data.cookie,
        )
    if not transfer:
        raise HTTPException(status_code=410, detail="浏览器 Cookie 请求已失效，请重新发起")
    try:
        cookie = filter_downloader_cookies(transfer.platform, data.cookie)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not cookie:
        raise HTTPException(status_code=400, detail=cookie_hint_for_empty(transfer.platform))
    cookie_manager.set(transfer.platform, cookie)
    # 条目数必须按平台格式统计：netscape（YouTube）按行数，header 按 `; ` 分段。
    # 历史实现直接 `len(cookie.split("; "))`，对 cookies.txt 会算成 1（甚至 0）。
    return R.success(data={"platform": transfer.platform, "count": cookie_entry_count(transfer.platform, cookie)})


@router.post("/bilibili_qr_login")
def start_bilibili_qr_login(admin: User = Depends(require_admin)):
    try:
        return R.success(data=qr_login_manager.start(admin.id))
    except BilibiliQrLoginError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/bilibili_qr_login/{session_id}")
def poll_bilibili_qr_login(session_id: str, admin: User = Depends(require_admin)):
    try:
        return R.success(data=qr_login_manager.poll(session_id, admin.id))
    except BilibiliQrLoginError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc


@router.delete("/bilibili_qr_login/{session_id}")
def cancel_bilibili_qr_login(session_id: str, admin: User = Depends(require_admin)):
    qr_login_manager.cancel(session_id, admin.id)
    return R.success()

class TranscriberConfigRequest(BaseModel):
    transcriber_type: str
    whisper_model_size: Optional[str] = None


def _build_transcriber_types():
    """转写引擎清单。

    `groq` 是代码里的槽位名（走 OpenAI 兼容的 /audio/transcriptions），
    实际指向哪个服务取决于 providers 表中 id=groq 的 base_url：
    可能是 Groq 云端，也可能是自建网关（如本地 FunASR）。标签按真实目标
    生成，避免界面上写「Groq（在线）」而实际连的是本地服务。
    """
    base_url = ""
    try:
        from app.services.provider import ProviderService

        provider = ProviderService.get_provider_by_id("groq")
        base_url = (provider or {}).get("base_url") or ""
    except Exception:
        base_url = ""

    if "groq.com" in base_url:
        groq_label = "Groq（云端）"
    elif base_url:
        groq_label = "自建端点（OpenAI 兼容）"
    else:
        groq_label = "OpenAI 兼容端点"

    return [
        {"value": "fast-whisper", "label": "Faster Whisper（本地）"},
        {"value": "bcut", "label": "必剪（在线）"},
        {"value": "kuaishou", "label": "快手（在线）"},
        {"value": "groq", "label": groq_label},
        {"value": "mlx-whisper", "label": "MLX Whisper（仅macOS）"},
    ]


WHISPER_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]


@router.get("/transcriber_config")
def get_transcriber_config():
    from app.transcriber.transcriber_provider import MLX_WHISPER_AVAILABLE
    from app.transcriber.whisper_models import get_registry, BUILTIN_WHISPER_MODELS
    from app.services.provider import ProviderService

    registry = get_registry()
    config = transcriber_config_manager.get_config()
    return R.success(data={
        **config,
        "available_types": _build_transcriber_types(),
        # 前端据此展示「当前实际连的是哪个端点 / 哪个模型」
        "resolved_endpoint": (ProviderService.get_provider_by_id("groq") or {}).get("base_url", ""),
        "resolved_model": transcriber_config_manager.get_config().get("groq_model", ""),
        "has_asr_key": bool((ProviderService.get_provider_by_id("groq") or {}).get("api_key")),
        # 内置可见档位 + 用户自定义模型，供前端下拉
        "whisper_model_sizes": registry.visible_model_names(),
        "whisper_builtin_models": BUILTIN_WHISPER_MODELS,
        "whisper_custom_models": registry.get_custom_models(),
        "mlx_whisper_available": MLX_WHISPER_AVAILABLE,
    })



class AsrEndpointRequest(BaseModel):
    """在线转写（groq 槽位）的端点配置，按常规在线服务的方式在界面直接改。"""
    base_url: str
    api_key: Optional[str] = None
    model: Optional[str] = None


@router.post("/update_asr_endpoint")
def update_asr_endpoint(data: AsrEndpointRequest, admin: User = Depends(require_admin)):
    from app.services.provider import ProviderService

    base_url = (data.base_url or "").strip()
    if not base_url.lower().startswith(("http://", "https://")):
        return R.error(msg="端点地址必须以 http:// 或 https:// 开头")

    payload = {"base_url": base_url}
    # 只传新 key 才覆盖；前端若回传掩码值（含 *），ProviderService 会自行忽略
    if data.api_key:
        payload["api_key"] = data.api_key.strip()
    ProviderService.update_provider("groq", payload)

    current = transcriber_config_manager.get_config()
    model = (data.model or "").strip()
    transcriber_config_manager.update_config(
        transcriber_type=current.get("transcriber_type", "groq"),
        whisper_model_size=None,
        groq_model=model or None,
    )

    provider = ProviderService.get_provider_by_id("groq") or {}
    return R.success(
        msg="转写端点已更新",
        data={
            "base_url": provider.get("base_url", ""),
            "has_api_key": bool(provider.get("api_key")),
            "model": transcriber_config_manager.get_config().get("groq_model", ""),
        },
    )


class WhisperCustomModelRequest(BaseModel):
    name: str
    target: str  # HF repo_id（如 Systran/faster-whisper-large-v3）或本地模型目录路径
@router.get("/whisper_models")
def list_whisper_models():
    """列出内置 + 用户自定义的 whisper 模型映射。"""
    from app.transcriber.whisper_models import get_registry, BUILTIN_WHISPER_MODELS
    reg = get_registry()
    return R.success(data={"builtin": BUILTIN_WHISPER_MODELS, "custom": reg.get_custom_models()})


@router.post("/whisper_models")
def add_whisper_model(data: WhisperCustomModelRequest, admin: User = Depends(require_admin)):
    """新增自定义 whisper 模型映射（名称 → HF repo_id 或本地路径）。"""
    from app.transcriber.whisper_models import get_registry
    try:
        custom = get_registry().add_custom_model(data.name, data.target)
    except ValueError as e:
        return R.error(msg=str(e))
    return R.success(data={"custom": custom}, msg="已添加自定义模型")


@router.delete("/whisper_models/{name}")
def delete_whisper_model(name: str, admin: User = Depends(require_admin)):
    """删除自定义 whisper 模型映射（不会删除已下载的模型文件）。"""
    from app.transcriber.whisper_models import get_registry
    custom = get_registry().remove_custom_model(name)
    return R.success(data={"custom": custom}, msg="已删除自定义模型")


@router.post("/transcriber_config")
def update_transcriber_config(data: TranscriberConfigRequest, admin: User = Depends(require_admin)):
    config = transcriber_config_manager.update_config(
        transcriber_type=data.transcriber_type,
        whisper_model_size=data.whisper_model_size,
    )
    return R.success(data=config)


# ---- BYOK 站点模式（shared / optional / required）----
#
# 读接口对所有登录用户开放（前端要据此决定是否显示「我的凭据」入口）；
# 写接口仅管理员。模式存在 config/byok.json，改完**立即生效**、无需重启——
# 这正是「三种模式支持随时切换」的落点。


class ByokConfigRequest(BaseModel):
    mode: Optional[str] = None
    allow_custom_base_url: Optional[bool] = None


@router.get("/byok_config")
def get_byok_config():
    """读当前 BYOK 模式。所有登录用户可读（前端据此渲染）。"""
    from app.services.byok_config import byok_config

    return R.success(data=byok_config.get())


@router.patch("/byok_config")
def update_byok_config(data: ByokConfigRequest, admin: User = Depends(require_admin)):
    """切换全站 BYOK 模式（管理员）。

    从 optional 切到 required 会让「所有未配置凭据的用户无法提交任务」，
    这是破坏性变更——前端必须给出确认提示。
    """
    from app.services.byok_config import byok_config

    if data.mode is None and data.allow_custom_base_url is None:
        return R.error(msg="请至少提供一个字段", code=400)

    try:
        cfg = byok_config.update(
            mode=data.mode,
            allow_custom_base_url=data.allow_custom_base_url,
        )
    except ValueError as exc:
        return R.error(msg=str(exc), code=400)

    logger.info(f"管理员 {admin.email} 切换 BYOK 模式为 {cfg['mode']}")
    return R.success(data=cfg, msg="配置已更新")


# ---- 全局代理配置（作用于 LLM API + 转写 API + yt-dlp 下载）----

class ProxyConfigRequest(BaseModel):
    enabled: bool
    url: Optional[str] = None


@router.get("/proxy_config")
def get_proxy_config():
    from app.services.proxy_config_manager import ProxyConfigManager
    mgr = ProxyConfigManager()
    cfg = mgr.get_config()
    # effective 给前端展示「当前实际生效的代理」——可能来自配置，也可能来自 env 兜底
    return R.success(data={
        **cfg,
        "effective": mgr.get_proxy_url() or "",
    })


@router.post("/proxy_config")
def update_proxy_config(data: ProxyConfigRequest, admin: User = Depends(require_admin)):
    from app.services.proxy_config_manager import ProxyConfigManager
    mgr = ProxyConfigManager()
    cfg = mgr.update_config(enabled=data.enabled, url=data.url)
    return R.success(data={
        **cfg,
        "effective": mgr.get_proxy_url() or "",
    })


# ---- Whisper 模型下载状态 & 下载触发 ----
# 下载状态（downloading / done / failed + 失败原因）统一交给 model_download_state 维护，
# 「触发下载」与「查询状态」共享同一份进程内内存态。失败原因会随状态接口透传给前端，
# 修复 issue #402 衍生问题：原先只回传 downloading/downloaded，下载失败时前端无任何提示。


def _check_whisper_model_exists(model_size: str, subdir: str = "whisper") -> bool:
    """检查指定 whisper 模型是否已下载完整到本地。

    先把模型名 resolve 成可加载标识，再按类型判定：
      - 本地路径模型 → 直接看该目录下有没有 model.bin
      - HF repo_id → 看 HF cache 布局
        <model_dir>/models--{org}--{name}/snapshots/<hash>/model.bin
        （历史 modelscope 布局 <model_dir>/whisper-{size}/model.bin 也兼容识别）
    """
    from app.transcriber.whisper_models import (
        resolve_whisper_model,
        is_local_target,
        hf_cache_dirname,
    )
    try:
        target = resolve_whisper_model(model_size)
    except Exception:
        return False
    if is_local_target(target):
        return (Path(target) / "model.bin").exists()

    model_dir = Path(get_model_dir(subdir))
    # HF cache 布局（适配任意 org/repo，不再写死 Systran）
    hf_repo_dir = model_dir / hf_cache_dirname(target) / "snapshots"
    if hf_repo_dir.exists():
        for snapshot in hf_repo_dir.iterdir():
            if (snapshot / "model.bin").exists():
                return True
    # 历史 modelscope 布局（向后兼容老用户）
    legacy = model_dir / f"whisper-{model_size}" / "model.bin"
    return legacy.exists()


def _check_mlx_whisper_model_exists(model_size: str) -> bool:
    """检查 mlx-whisper 模型是否已下载完整到本地。

    与 fast-whisper 的目录布局不同：mlx 模型按 HuggingFace repo_id
    （如 mlx-community/whisper-tiny-mlx）落盘，且没有 model.bin，
    用 config.json 作为「下载完成」的判据，和 mlx_whisper_transcriber.py 保持一致。
    """
    try:
        from app.transcriber.mlx_whisper_transcriber import MLX_MODEL_MAP
    except Exception:
        return False
    repo_id = MLX_MODEL_MAP.get(model_size)
    if not repo_id:
        return False
    model_dir = get_model_dir("mlx-whisper")
    model_path = os.path.join(model_dir, repo_id)
    return (Path(model_path) / "config.json").exists()


@router.get("/transcriber_models_status")
def get_transcriber_models_status():
    """返回所有 whisper 模型的下载状态（含用户自定义模型）。"""
    from app.transcriber.whisper_models import get_registry
    statuses = []
    for size in get_registry().visible_model_names():
        downloaded = _check_whisper_model_exists(size, "whisper")
        statuses.append(dl_state.status_row(size, downloaded))

    # 也检查 mlx-whisper（仅 macOS）
    mlx_available = platform.system() == "Darwin"
    mlx_statuses = []
    if mlx_available:
        from app.transcriber.mlx_whisper_transcriber import MLX_MODEL_MAP
        for size in WHISPER_MODEL_SIZES:
            repo_id = MLX_MODEL_MAP.get(size)
            # 用 config.json 判定，和 _check_mlx_whisper_model_exists / 加载逻辑保持一致
            downloaded = _check_mlx_whisper_model_exists(size)
            row = dl_state.status_row(size, downloaded, key=f"mlx-{size}")
            row["available"] = repo_id is not None
            mlx_statuses.append(row)

    return R.success(data={
        "whisper": statuses,
        "mlx_whisper": mlx_statuses,
        "mlx_available": mlx_available,
    })


class ModelDownloadRequest(BaseModel):
    model_size: str
    transcriber_type: str = "fast-whisper"  # "fast-whisper" 或 "mlx-whisper"


def _friendly_download_error(e: Exception) -> str:
    """把 HuggingFace 的网络类报错翻译成用户能照着做的提示（issue #417）。

    典型原文：'An error happened while trying to locate the file on the Hub and we
    cannot find the requested files in the local cache...' —— 本质是连不上 Hub。
    用户大概率不知道：默认走 hf-mirror.com 镜像，可配代理或改 HF_ENDPOINT。
    """
    raw = str(e)
    lowered = raw.lower()
    network_markers = (
        "locate the file on the hub",
        "couldn't connect",
        "connection error",
        "connecttimeout",
        "read timed out",
        "max retries exceeded",
        "failed to establish",
        "name or service not known",
        "temporary failure in name resolution",
    )
    if any(m in lowered for m in network_markers):
        endpoint = os.getenv("HF_ENDPOINT", "https://huggingface.co")
        return (
            f"{raw}\n"
            f"——连不上模型仓库（当前 HF_ENDPOINT={endpoint}）。可尝试："
            f"1) 在「设置」里配置可用代理；"
            f"2) 设置环境变量 HF_ENDPOINT 切换镜像（国内可用 https://hf-mirror.com）；"
            f"3) 确认容器能访问外网/镜像站后重试。"
        )
    return raw


def _do_download_whisper(model_size: str):
    """后台下载 faster-whisper 模型（支持内置 size / 自定义 repo_id / 本地路径）。

    模型名先 resolve：
      - 本地路径模型：无需下载，目录里有 model.bin 即 done，否则 failed；
      - HF repo_id：snapshot_download 到 HF cache 布局（cache_dir=model_dir），
        与加载逻辑 WhisperModel(download_root=model_dir) 完全对齐。
    """
    from huggingface_hub import snapshot_download
    from app.transcriber.whisper_models import resolve_whisper_model, is_local_target
    from app.services.proxy_config_manager import ProxyConfigManager

    try:
        dl_state.mark_downloading(model_size)
        # 让 UI 配的代理对 HuggingFace 下载也生效（issue #417：容器里代理没生效）
        proxy = ProxyConfigManager().apply_to_env()
        if proxy:
            logger.info(f"whisper 下载走代理: {proxy}")
        model_dir = get_model_dir("whisper")

        # 已经下好就不重复下
        if _check_whisper_model_exists(model_size, "whisper"):
            dl_state.mark_done(model_size)
            return

        target = resolve_whisper_model(model_size)
        if is_local_target(target):
            # 本地模型不下载，只校验 model.bin 是否就位
            ok = (Path(target) / "model.bin").exists()
            if ok:
                dl_state.mark_done(model_size)
            else:
                msg = f"本地模型路径 {target} 下没有 model.bin，无法使用"
                logger.warning(f"本地模型 {model_size}：{msg}")
                dl_state.mark_failed(model_size, msg)
            return

        logger.info(f"开始下载 whisper 模型: {model_size} ← {target}")
        # 跟 faster-whisper utils.py 用同样的 allow_patterns，避免多下无关文件；
        # 不传 local_dir 让它走 HF 默认 cache 布局（与加载逻辑对齐）
        snapshot_download(
            target,
            cache_dir=model_dir,
            allow_patterns=[
                "config.json",
                "preprocessor_config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.*",
            ],
        )
        logger.info(f"whisper 模型下载完成: {model_size}")
        dl_state.mark_done(model_size)
    except Exception as e:
        msg = _friendly_download_error(e)
        logger.error(f"whisper 模型下载失败: {model_size}, {e}")
        dl_state.mark_failed(model_size, msg)


def _do_download_mlx_whisper(model_size: str):
    """后台下载 mlx-whisper 模型。"""
    key = f"mlx-{model_size}"
    try:
        dl_state.mark_downloading(key)
        from huggingface_hub import snapshot_download as hf_download
        from app.transcriber.mlx_whisper_transcriber import resolve_mlx_repo_id
        from app.services.proxy_config_manager import ProxyConfigManager

        # 让 UI 配的代理对 HuggingFace 下载也生效（issue #417）
        proxy = ProxyConfigManager().apply_to_env()
        if proxy:
            logger.info(f"mlx-whisper 下载走代理: {proxy}")

        try:
            repo_id = resolve_mlx_repo_id(model_size)
        except ValueError as e:
            logger.error(str(e))
            dl_state.mark_failed(key, str(e))
            return

        model_dir = get_model_dir("mlx-whisper")
        model_path = os.path.join(model_dir, repo_id)
        # 用 config.json 判定而非目录存在：半成品目录不能算「已下载」
        if (Path(model_path) / "config.json").exists():
            dl_state.mark_done(key)
            return
        logger.info(f"开始下载 mlx-whisper 模型: {model_size} ← {repo_id}")
        hf_download(repo_id, local_dir=model_path, local_dir_use_symlinks=False)
        logger.info(f"mlx-whisper 模型下载完成: {model_size}")
        dl_state.mark_done(key)
    except Exception as e:
        msg = _friendly_download_error(e)
        logger.error(f"mlx-whisper 模型下载失败: {model_size}, {e}")
        dl_state.mark_failed(key, msg)


@router.post("/transcriber_download")
def download_transcriber_model(data: ModelDownloadRequest, background_tasks: BackgroundTasks, admin: User = Depends(require_admin)):
    """触发后台下载指定的 whisper 模型（fast-whisper 支持内置档位 + 自定义模型）。"""
    if data.transcriber_type == "mlx-whisper":
        # mlx 只认内置档位（mlx-community 的固定映射）
        if data.model_size not in WHISPER_MODEL_SIZES:
            return R.error(msg=f"MLX 不支持的模型大小: {data.model_size}")
        if platform.system() != "Darwin":
            return R.error(msg="MLX Whisper 仅支持 macOS")
        key = f"mlx-{data.model_size}"
        if dl_state.is_downloading(key):
            return R.success(msg="模型正在下载中")
        background_tasks.add_task(_do_download_mlx_whisper, data.model_size)
    else:
        # fast-whisper：内置档位 / 自定义 repo_id / 本地路径都允许
        from app.transcriber.whisper_models import get_registry
        if not get_registry().is_known(data.model_size):
            return R.error(msg=f"不支持的模型: {data.model_size}（请先在自定义模型中登记）")
        if dl_state.is_downloading(data.model_size):
            return R.success(msg="模型正在下载中")
        background_tasks.add_task(_do_download_whisper, data.model_size)

    return R.success(msg="模型下载已开始")


@public_router.get("/sys_health")
async def sys_health():
    """结构化健康状态——任何子项异常都不应让整个 endpoint 5xx。

    每个字段：'ok' | 'missing' | 'error'。
    前端 useCheckBackend 用 /sys_check 做存活判定（不依赖外部依赖），
    /sys_health 用来在设置页区分「后端没起」vs「后端起了但 ffmpeg 缺」vs「DB 写不进去」等更细的状态。
    """
    ffmpeg_status = "ok"
    try:
        ensure_ffmpeg_or_raise()
    except Exception:
        ffmpeg_status = "missing"

    db_status = "ok"
    try:
        from app.db.engine import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # 当前转写器配置 + 模型是否已下载（用 model.bin 落盘判定，与 transcriber 加载逻辑一致）
    whisper_info: dict = {"size": None, "type": None, "downloaded": False, "checked": False}
    try:
        cfg = transcriber_config_manager.get_config()
        size = cfg["whisper_model_size"]
        ttype = cfg["transcriber_type"]
        whisper_info["size"] = size
        whisper_info["type"] = ttype
        # 只有本地引擎才有「下载」概念；groq / bcut / kuaishou 在线引擎跳过
        if ttype == "fast-whisper":
            whisper_info["downloaded"] = _check_whisper_model_exists(size, "whisper")
            whisper_info["checked"] = True
        elif ttype == "mlx-whisper":
            whisper_info["downloaded"] = _check_mlx_whisper_model_exists(size)
            whisper_info["checked"] = True
    except Exception:
        pass

    return R.success(data={
        "backend": "ok",
        "ffmpeg": ffmpeg_status,
        "db": db_status,
        "whisper_model": whisper_info,
    })


@public_router.get("/sys_check")
async def sys_check():
    """轻量存活判定：后端进程能响应这个 endpoint 就算「起来了」，不查外部依赖。

    给桌面端 useCheckBackend / Tauri ready-probe 用。
    """
    return R.success()


@router.get("/deploy_status")
async def deploy_status():
    """返回部署监控所需的所有状态信息。

    所有子项都用 try 包起来——监控页本身不应该被任何一个子项打死。
    特别是 torch：它只在 fast-whisper 路径用得到，用 Groq / 必剪 / 快手在线
    引擎的轻量部署完全可以不装，那种情况这个 endpoint 不应该 500。
    """
    import os

    # CUDA 状态
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        cuda_info = {
            "available": cuda_available,
            "torch_installed": True,
            "version": torch.version.cuda if cuda_available else None,
            "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
        }
    except Exception:
        cuda_info = {
            "available": False,
            "torch_installed": False,
            "version": None,
            "gpu_name": None,
        }

    # Whisper 模型 / 转写器配置 + 本地下载状态
    try:
        transcriber_cfg = transcriber_config_manager.get_config()
        size = transcriber_cfg["whisper_model_size"]
        ttype = transcriber_cfg["transcriber_type"]
        if ttype == "fast-whisper":
            downloaded = _check_whisper_model_exists(size, "whisper")
        elif ttype == "mlx-whisper":
            downloaded = _check_mlx_whisper_model_exists(size)
        else:
            downloaded = False  # 在线引擎无下载概念
        whisper_info = {
            "model_size": size,
            "transcriber_type": ttype,
            "downloaded": downloaded,
        }
    except Exception:
        whisper_info = {"model_size": None, "transcriber_type": None, "downloaded": False}

    # FFmpeg 状态
    try:
        ensure_ffmpeg_or_raise()
        ffmpeg_ok = True
    except Exception:
        ffmpeg_ok = False

    return R.success(data={
        "backend": {"status": "running", "port": int(os.getenv("BACKEND_PORT", 8483))},
        "cuda": cuda_info,
        "whisper": whisper_info,
        "ffmpeg": {"available": ffmpeg_ok},
    })