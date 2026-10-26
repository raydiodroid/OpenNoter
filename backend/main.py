import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles
from dotenv import load_dotenv

from app.db.init_db import init_db
from app.db.provider_dao import seed_default_providers
from app.exceptions.exception_handlers import register_exception_handlers
from app.middlewares.api_key_usage import ApiKeyUsageMiddleware
from app.services.api_key_runtime import usage_logger
from app.services.auth import flush_all_activity
# from app.db.model_dao import init_model_table
# from app.db.provider_dao import init_provider_table
from app.utils.logger import get_logger
from app import create_app
from app.services.transcriber_config_manager import TranscriberConfigManager
from events import register_handler
from ffmpeg_helper import ensure_ffmpeg_or_raise

logger = get_logger(__name__)
load_dotenv()

# 读取 .env 中的路径
out_dir = os.getenv('OUT_DIR', './static/screenshots')

# 自动创建本地目录（static 和 static/screenshots）
static_dir = "static"
uploads_dir = "uploads"
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

if not os.path.exists(out_dir):
    os.makedirs(out_dir)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动序列拆成 5 步、每步独立日志 + 异常时打明确的 [startup N/5 FAILED] 标记。
    # 目的：用户 docker logs 一眼能看出后端死在哪一步，避免「容器一直重启但看不出原因」。
    try:
        logger.info("[startup 1/5] register_handler() — 注册事件处理器")
        register_handler()

        logger.info("[startup 2/5] init_db() — 初始化 SQLite 数据库")
        init_db()

        logger.info("[startup 3/5] TranscriberConfigManager — 读取转写器配置")
        # 转写器不再在启动时强制初始化，而是在首次生成笔记时按需创建。
        # 如果配置了不可用的类型（如 mlx-whisper 未安装），会在使用时报错而非静默回退。
        _cfg = TranscriberConfigManager().get_config()
        logger.info(
            f"           当前转写器: type={_cfg['transcriber_type']}, "
            f"model_size={_cfg['whisper_model_size']}"
        )

        logger.info("[startup 4/5] seed_default_providers() — 初始化默认 LLM 供应商")
        seed_default_providers()

        # 把已配置的代理 export 到环境变量，让 huggingface_hub（whisper 模型下载）
        # 也能走代理——含转写时的按需下载（issue #417）。
        from app.services.proxy_config_manager import ProxyConfigManager
        _proxy = ProxyConfigManager().apply_to_env()
        if _proxy:
            logger.info(f"           已应用全局代理到环境变量: {_proxy}")

        # 静默把历史明文 Cookie 转成密文（幂等）。
        # 放在这里而不是首次读写时：避免「读一次写一次」的隐式落盘，
        # 也让升级后第一时间消除明文。
        try:
            from app.services.cookie_manager import CookieConfigManager

            migrated = CookieConfigManager().migrate_plaintext()
            if migrated:
                logger.info(f"           已加密 {migrated} 个平台的存量 Cookie（明文 → 密文）")
        except Exception as exc:
            # 迁移失败不该阻止启动；下次再试
            logger.warning(f"           Cookie 明文迁移失败（下次启动重试）：{exc}")

        # 静默把 providers.api_key 存量明文转成密文（幂等；复用 secret_box / Fernet）。
        try:
            from app.db.provider_dao import migrate_plaintext_api_keys

            migrated_keys = migrate_plaintext_api_keys()
            if migrated_keys:
                logger.info(
                    f"           已加密 {migrated_keys} 个 Provider api_key（明文 → 密文）"
                )
        except Exception as exc:
            logger.warning(f"           Provider api_key 明文迁移失败（下次启动重试）：{exc}")

        logger.info("[startup 5/5] 启动完成，等待请求")

        # 上次进程可能被 SIGKILL/OOM 干掉，shutdown 钩子没跑到：把残留进行中任务标 FAILED。
        try:
            from app.services.task_interrupt import (
                DEFAULT_STARTUP_MESSAGE,
                fail_interrupted_tasks,
            )

            n = fail_interrupted_tasks(reason=DEFAULT_STARTUP_MESSAGE)
            if n:
                logger.info(f"           启动清理：{n} 个中断任务已标 FAILED")
        except Exception as exc:
            logger.warning(f"           启动清理中断任务失败：{exc}")

        # F49：启动一个后台协程，定期清理「运行期」产生的僵尸任务。
        #
        # 启动时的 fail_interrupted_tasks 只覆盖「进程正常重启」。若进程被
        # kill -9 / OOM killer 杀掉，后来的那个进程在启动时确实能清掉旧僵尸；
        # 但**同一个进程运行期间**也可能产生僵尸（例如某个任务的工作线程
        # 因未捕获异常静默死掉，而状态文件没被更新）—— 那种只有定期扫描能发现。
        try:
            import asyncio

            async def _stale_task_reaper():
                interval = int(os.getenv("TASK_REAP_INTERVAL_SECONDS", "300"))
                # 先等一会儿再开始扫：刚启动时线程池是空的，
                # 立刻扫会把「刚提交、还没来得及登记」的任务误判成僵尸。
                await asyncio.sleep(min(60, interval))
                while True:
                    try:
                        from app.services.task_interrupt import reap_stale_tasks

                        stats = await asyncio.to_thread(
                            reap_stale_tasks, dry_run=False,
                        )
                        if stats.get("reaped"):
                            logger.info(f"           僵尸任务清理：{stats['reaped']} 个")
                    except Exception as exc:  # noqa: BLE001 - 清理失败不能打死整个循环
                        logger.warning(f"           僵尸任务清理失败：{exc}")
                    await asyncio.sleep(interval)

            app.state.stale_reaper_task = asyncio.create_task(_stale_task_reaper())
            logger.info("           已启动僵尸任务定期清理（默认每 300 秒）")
        except Exception as exc:  # noqa: BLE001 - 起不来也不该让服务启动失败
            logger.warning(f"           启动僵尸任务清理失败（不影响主服务）：{exc}")

    except Exception:
        logger.exception("[startup FAILED] 后端启动期异常，详见堆栈；容器会退出并由 restart 策略决定是否重试")
        raise

    # 用量日志写入线程（启动序列之外，失败不影响主流程）
    try:
        usage_logger.start()
    except Exception:
        logger.warning("API Key 用量日志线程启动失败，用量统计将不可用", exc_info=True)

    yield

    # 优雅退出：进行中的 BackgroundTasks 不会被续跑，先标 FAILED 避免前端空转。
    try:
        from app.services.task_interrupt import (
            DEFAULT_SHUTDOWN_MESSAGE,
            fail_interrupted_tasks,
        )

        fail_interrupted_tasks(reason=DEFAULT_SHUTDOWN_MESSAGE)
    except Exception:
        logger.warning("退出前标记中断任务失败", exc_info=True)

    # 退出前把活动记录缓冲刷进库，别丢掉最后一段使用痕迹
    try:
        from app.db.engine import SessionLocal

        db = SessionLocal()
        try:
            flush_all_activity(db)
        finally:
            db.close()
    except Exception:
        logger.warning("退出前刷新 API Key 活动记录失败", exc_info=True)
    try:
        usage_logger.stop()
    except Exception:
        pass

app = create_app(lifespan=lifespan)

# CORS：本地 web + Tauri 始终放行；浏览器扩展默认只放行
# OPENNOTER_CORS_EXTENSION_IDS 白名单（生产）。开发版 ID 不固定时再开
# OPENNOTER_CORS_ALLOW_ANY_EXTENSION=true（切勿用于公网）。
# Tauri 2 各平台 origin：tauri://localhost / https://tauri.localhost / http://tauri.localhost。
from app.utils.cors_origins import build_cors_origin_regex

CORS_ORIGIN_REGEX = build_cors_origin_regex()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
# API Key 用量日志：读 request.state.api_key（鉴权依赖挂上去的），异步写明细
app.add_middleware(ApiKeyUsageMiddleware)
register_exception_handlers(app)


# /static：**不走匿名 StaticFiles**，改为鉴权路由（R1）。
#
# 背景：截图（含视频画面）属于用户笔记内容。原先 app.mount(StaticFiles)
# 意味着任何人只要猜到 URL 就能读他人的截图；虽然文件名带 UUID 难猜，
# 但 URL 会出现在笔记 markdown（分享、导出、日志）里，不是可靠访问控制。
#
# 归属判定策略（渐进式，避免老数据全 404）：
#   - 新格式 `screenshots/<task_id>/xxx.jpg`：从路径取 task_id，复用
#     _can_access_task 做归属校验
#   - 旧格式 `screenshots/xxx.jpg`（历史遗留，文件名不含 task_id）：
#     无法反查归属，按「无归属」放行（与历史行为一致）
#   - `AUTH_ENFORCE=false`（单机自托管）时一律放行
@app.get("/static/{file_path:path}")
async def serve_static(file_path: str, request: Request):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from app.db.engine import SessionLocal
    from app.services.auth import auth_enforced, get_optional_user
    from app.routers.note import _can_access_task
    from app.utils.path_safety import resolve_under_dir
    from app.utils.static_signing import (
        TOKEN_PARAM,
        has_token,
        static_resource_key,
        verify_resource_token,
        verify_token,
    )

    # 从路径推断 task_id：screenshots/<task_id>/<name>.<ext>
    #
    # ⚠️ 必须基于**解析后的真实路径**，不能用 URL 里的字面量。
    #
    # 历史实现用 `Path(file_path).parts[1]` 取 task_id，但真正的文件读取走
    # `resolve_under_dir()`（会折叠 `..`）。两者不是同一个对象，于是出现
    # 授权对象 ≠ 读取对象的经典绕过：
    #
    #     /static/screenshots/A/%2e%2e/B/b.jpg?t=<A 的令牌>
    #       → parts[1] = "A"        ⇒ 令牌校验通过（授权给 A）
    #       → resolve 后指向 B/b.jpg ⇒ 实际读到 B 的文件
    #
    # 明文 `..` 会被 Starlette 规范化掉，但 **URL 编码的 `%2e%2e`** 会原样
    # 传进来，再由 `Path.resolve()` 折叠 —— 实测可读其它 task 的截图，
    # 甚至用单张令牌读 `static/` 根下的任意文件。
    #
    # 修法：先 resolve（越界直接 404），再从**真实路径**相对 static_root
    # 反推 task_id。这样授权对象与读取对象必然一致，且签名模式与 Cookie 模式
    # 同时修好（旧逻辑在 F9 之前就有同样的不一致，只是危害面小）。
    from pathlib import Path as _Path

    static_root = _Path(static_dir).resolve()
    try:
        target = resolve_under_dir(static_root, file_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    path_task_id = None
    # 令牌绑定对象：("task", task_id) 或 ("cover", filename) 或 None（不需令牌）
    resource_spec = None
    try:
        rel_parts = target.relative_to(static_root).parts
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 一律用**真实相对路径**反推，保证与上面 resolve 出来的 target 完全一致。
    #
    # 注意要拼回 `/static/` 前缀：rel_parts 是 target 相对 static_root 的段
    # （形如 ("screenshots","<task>","x.jpg")），而 static_resource_key()
    # 认的是完整 URL 路径（`/static/screenshots/...`）。漏了这层前缀会让
    # 所有令牌都解析不出绑定对象 → 正确令牌也被判 403。
    rel_path = "/static/" + "/".join(rel_parts)
    if len(rel_parts) >= 3 and rel_parts[0] == "screenshots":
        candidate = rel_parts[1]
        # task_id 形态：UUID 或简单标识；文件名本身（含 .jpg）不是 task_id
        if "." not in candidate:
            path_task_id = candidate
    # 静态路径 → 令牌绑定键（截图按 task、封面按文件名）
    resource_spec = static_resource_key(rel_path)

    # ------------------------------------------------------------------
    # 1) 先验令牌（必须在 get_optional_user **之前**）
    # ------------------------------------------------------------------
    # 为什么顺序不能反：`<img src>` 是浏览器发起的裸 GET，**不带 Cookie 也不带
    # Authorization**。而 `get_optional_user` 在 AUTH_ENFORCE 下遇到无凭据会直接
    # raise 401 —— 如果先调它，Tauri/扩展的截图请求会先吃 401，令牌校验**永远
    # 轮不到执行**。那样表面上看"已经加了签名"，实际截图依然全挂。
    #
    # 令牌语义（见 app/utils/static_signing.py）：
    #   - 是 per-task 的**长期**令牌，不含时间成分。因为截图 URL 在生成笔记时
    #     就被写进 markdown 并持久化（note_results/*.json + 前端 IndexedDB），
    #     带过期的签名会让历史笔记的截图永久破图。
    #   - 拿到 URL 即可读该 task 的截图（能力凭证），与 task_id 的暴露面相当。
    token_present = has_token(request.query_params)
    if token_present:
        supplied = request.query_params.get(TOKEN_PARAM, "")
        # target 已在上方 resolve 并校验过存在性（基于真实路径反推），
        # 这里不再重复 resolve —— 重复一次只会让「授权对象 vs 读取对象」再次分叉。
        if resource_spec is not None:
            resource, key_value = resource_spec
            if verify_resource_token(resource, key_value, supplied):
                return FileResponse(target)
        # 带了令牌但不匹配/已停用 —— 用 403 而不是 401。
        #
        # 401 会触发前端 lib/api.ts 的"跳登录页"拦截器：用户只是打开一篇旧笔记
        # 看截图，就被踢下线，是极难排查的体验事故。403 会被当作普通业务错误
        # （图片位置显示裂图），不影响登录态。
        raise HTTPException(status_code=403, detail="图片链接无效或已过期")

    # ------------------------------------------------------------------
    # 2) 没带令牌 → 走原有的凭据鉴权（Cookie / API-Key / Bearer）
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        user = get_optional_user(request, db)
    finally:
        db.close()

    if auth_enforced():
        if path_task_id is not None and not _can_access_task(path_task_id, user):
            raise HTTPException(status_code=403, detail="无权访问该图片")

    return FileResponse(target)


# /uploads 不再用匿名 StaticFiles：统一走鉴权下载，并强制路径落在 uploads 根内。
# AUTH_ENFORCE=false 时匿名仍可读（与本地上传→生成链路一致）；开启后需登录。
@app.get("/uploads/{file_path:path}")
async def serve_upload(file_path: str, request: Request):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from app.db.engine import SessionLocal
    from app.services.auth import get_optional_user
    from app.utils.upload_paths import resolve_under_uploads

    from app.services.auth import auth_enforced
    from app.utils.upload_paths import can_read_upload

    db = SessionLocal()
    try:
        # 复用 get_optional_user：强制认证时未登录 → 401
        user = get_optional_user(request, db)
    finally:
        db.close()

    try:
        target = resolve_under_uploads(f"/uploads/{file_path}")
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 归属校验（R2）：登录只解决「你是谁」，不解决「这个文件是不是你的」。
    # 历史文件没有侧车，按无归属放行，避免老部署全部 404。
    if not can_read_upload(target, user, auth_enforced=auth_enforced()):
        raise HTTPException(status_code=403, detail="无权访问该文件")

    return FileResponse(target)









if __name__ == "__main__":
    port = int(os.getenv("BACKEND_PORT", 8483))
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=False)