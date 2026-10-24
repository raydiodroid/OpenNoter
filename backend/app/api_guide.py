"""免鉴权的 API 使用指引。

问题：agent 拿到服务地址但不知道鉴权怎么写、流程长什么样时，撞到的只有一句
「未登录」或 404，等于没有任何信息。

这里给一个自描述的入口：
  - GET /api      返回机器可读的 JSON 指引（Accept: text/markdown 返回 Markdown）
  - 端点清单直接从 app.routes 生成，改了路由自动同步，不会过期

另外，401 响应里也会带上 auth_hint，指引怎么带凭据。
"""

from typing import Optional

import json
import os

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.services.auth import API_KEY_PREFIX, AUTH_HINT, get_current_user, get_optional_user
from app.utils.response import ResponseWrapper as R

API_VERSION = "2.0.0"

# 文档/指引里用到的一组固定文案
_AUTH_SECTION = {
    "required": True,
    "key_prefix": API_KEY_PREFIX,
    "schemes": [
        {
            "name": "API Key（脚本 / agent 用这个）",
            "headers": [f"x-api-key: {API_KEY_PREFIX}...", f"Authorization: Bearer {API_KEY_PREFIX}..."],
            "note": "两个请求头等价。key 以 sk-bili- 开头，和网页登录用的 JWT 不会混淆。",
        },
        {
            "name": "网页登录态（JWT）",
            "headers": ["Authorization: Bearer <JWT>", "Cookie: opennoter_token=<JWT>"],
        },
    ],
    "how_to_get_a_key": AUTH_HINT["how_to_get_a_key"],
}

_CONVENTIONS = {
    "response_shape": "所有响应统一为 {code, msg, data}。code == 0 表示成功；非 0 时 msg 是人类可读的失败原因，data 可能带结构化细节。",
    "http_status": "HTTP 状态码同时表达成败，可以直接据此判断：2xx 成功，4xx 参数或权限问题，5xx 服务端问题。不要只依赖 body.code。",
    "errors": "错误时 data 可能带细节，例如 {\"reason\": \"transcriber_model_not_ready\"} 表示转写模型还没下载好，"
              "{\"errors\": [...]} 是参数校验的逐字段说明。",
    "task_status_values": [
        "PENDING", "PARSING", "DOWNLOADING", "TRANSCRIBING",
        "SUMMARIZING", "FORMATTING", "SAVING", "SUCCESS", "FAILED",
    ],
    "task_status_note": "查询任务状态永远返回 200，任务本身的状态看 data.status。任务失败也是 200，看 data.message。",
}

_QUICKSTART = {
    "summary": "三步：建任务 → 轮询 → 取结果",
    "steps": [
        {
            "n": 1,
            "title": "创建笔记任务",
            "method": "POST",
            "path": "/api/notes",
            "body": {"url": "https://www.bilibili.com/video/BV1LLnj6fE1z"},
            "note": "只要给 url 就行。platform 按链接自动识别；provider_id / model_name 不传就自动挑已启用的供应商与模型，"
                    "传错了会当场 4xx（不会先收下再在后台失败）。",
            "returns": "data.task_id",
        },
        {
            "n": 2,
            "title": "轮询任务状态",
            "method": "GET",
            "path": "/api/tasks/{task_id}",
            "note": "每 5~10 秒查一次，直到 data.status 变成 SUCCESS 或 FAILED。",
            "returns": "data.status / data.message / data.result",
        },
        {
            "n": 3,
            "title": "取结果",
            "method": "GET",
            "path": "/api/tasks/{task_id}",
            "note": "成功时 data.result.markdown 是笔记正文，data.result.transcript 是字幕，data.result.audio_meta 是视频元信息。",
        },
    ],
    "curl": (
        "# 1. 建任务\n"
        "curl -X POST https://notes.example.com/api/notes \\\n"
        "  -H 'x-api-key: sk-bili-...' -H 'Content-Type: application/json' \\\n"
        "  -d '{\"url\":\"https://www.bilibili.com/video/BV1LLnj6fE1z\"}'\n\n"
        "# 2. 轮询（每 5~10 秒一次，直到 status 变成 SUCCESS / FAILED）\n"
        "curl -H 'x-api-key: sk-bili-...' https://notes.example.com/api/tasks/<task_id>"
    ),
}


def _route_is_public(route) -> bool:
    """路由是否免鉴权（挂了 get_optional_user / get_current_user / require_admin 的都要凭据）。"""
    auth_calls = {get_optional_user, get_current_user}
    try:
        from app.services.auth import require_admin

        auth_calls.add(require_admin)
    except Exception:  # pragma: no cover
        pass
    for dep in getattr(getattr(route, "dependant", None), "dependencies", []) or []:
        if dep.call in auth_calls:
            return False
    return True


def _collect_endpoints(app: FastAPI) -> list[dict]:
    """从实际路由表生成端点清单。

    include_in_schema=False 的是旧 RPC 风格路径，保留兼容但不出现在文档里，
    这里也不列 —— 展示的是推荐用法。
    """
    from fastapi.routing import APIRoute

    entries = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api"):
            continue
        if not route.include_in_schema:
            continue
        description = (route.description or "").strip().split("\n")[0]
        for method in sorted(route.methods or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            entries.append({
                "method": method,
                "path": route.path,
                "summary": description or route.summary,
                "auth": "none" if _route_is_public(route) else "required",
            })
    entries.sort(key=lambda e: (e["path"], e["method"]))
    return entries


def _abs_base_url(request: Request) -> str:
    """尽量还原对外可访问的基址（含协议）。

    优先级：
      1. OPENNOTER_PUBLIC_BASE_URL —— 显式配置，最可靠
      2. X-Forwarded-Proto / Forwarded 头
      3. 请求本身的 scheme

    为什么需要第 1 条：线上是 client → SafeLine(443) → NPMplus(80) → backend，
    NPMplus 会用 `$scheme` 覆盖 X-Forwarded-Proto（它自己收到的是 http），
    所以后端看到的协议永远是 http。代理链里再套一层 TLS 终止时尤其明显。
    """
    from app.utils.env_compat import getenv

    configured = (getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured

    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if "," in scheme:  # 多个代理各带一个，取第一个
        scheme = scheme.split(",")[0].strip()
    forwarded = request.headers.get("forwarded", "")
    if not scheme and forwarded:
        scheme = forwarded.split(";")[0].split("=")[-1].strip()

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


def build_guide(app: FastAPI, request: Optional[Request] = None) -> dict:
    base = _abs_base_url(request) if request is not None else ""
    quickstart = json.loads(json.dumps(_QUICKSTART))  # 深拷贝，避免改到模块级常量
    for step in quickstart["steps"]:
        # 给出完整 URL：调用方直接用，不用自己拼（拼容易把 /api 拼两遍）
        step["url"] = f"{base}{step['path']}" if base else step["path"]
    return {
        "service": "OpenNoter",
        "version": API_VERSION,
        "summary": "把 B 站 / YouTube / 抖音等视频转成结构化笔记的 API。给一个视频链接，异步生成笔记。",
        "base_url": base,
        "path_prefix": "/api",
        "url_convention": "所有 path 字段以站点根为起点（已含 /api 前缀）。完整 URL = base_url + path。",
        "authentication": _AUTH_SECTION,
        "quickstart": quickstart,
        "conventions": _CONVENTIONS,
        "endpoints": _collect_endpoints(app),
        "docs": {
            "openapi_json": "GET /openapi.json —— 完整 OpenAPI schema（需凭据）",
            "swagger_ui": "GET /docs —— Swagger UI（需凭据）",
        },
        "notes": [
            "本指引与 OpenAPI schema 均为只读描述，不提供任何写操作入口之外的权限。",
            "所有业务接口都要求凭据（OPENNOTER_AUTH_ENFORCE=1）。",
        ],
    }


def render_markdown(guide: dict) -> str:
    """把指引渲染成 Markdown，方便直接贴进 agent 的提示词。"""
    auth = guide["authentication"]
    conv = guide["conventions"]
    lines: list[str] = []
    add = lines.append

    add(f"# {guide['service']} API 使用指引")
    add("")
    add(guide["summary"])
    add("")
    if guide.get("base_url"):
        add(f"站点根地址：`{guide['base_url']}`")
        add("")
    add(f"> {guide['url_convention']}")
    add("")

    add("## 鉴权")
    add("")
    add("所有业务接口都需要凭据。两种写法任选其一：")
    add("")
    for scheme in auth["schemes"]:
        add(f"- **{scheme['name']}**")
        for header in scheme["headers"]:
            add(f"  - `{header}`")
        if scheme.get("note"):
            add(f"  - {scheme['note']}")
    add("")
    add(f"怎么拿 key：{auth['how_to_get_a_key']}")
    add("")

    add("## 快速上手")
    add("")
    for step in guide["quickstart"]["steps"]:
        add(f"### {step['n']}. {step['title']}")
        add("")
        if step.get("url"):
            add(f"`{step['method']} {step['url']}`")
        else:
            add(f"`{step['method']} {step['path']}`")
        add("")
        if step.get("body"):
            add("请求体：")
            add("")
            add("```json")
            add(_json_dump(step["body"]))
            add("```")
            add("")
        if step.get("note"):
            add(step["note"])
            add("")
        if step.get("returns"):
            add(f"返回：{step['returns']}")
            add("")

    add("## 约定")
    add("")
    add(f"- **响应结构**：{conv['response_shape']}")
    add(f"- **HTTP 状态码**：{conv['http_status']}")
    add(f"- **错误详情**：{conv['errors']}")
    add(f"- **任务状态取值**：{', '.join(conv['task_status_values'])}")
    add(f"- **任务状态说明**：{conv['task_status_note']}")
    add("")

    add("## 端点清单")
    add("")
    add("| 方法 | 路径 | 说明 | 鉴权 |")
    add("| --- | --- | --- | --- |")
    for ep in guide["endpoints"]:
        summary = (ep["summary"] or "").replace("|", "\\|")
        add(f"| {ep['method']} | `{ep['path']}` | {summary} | {ep['auth']} |")
    add("")

    add("## 完整 schema")
    add("")
    add(f"- {guide['docs']['openapi_json']}")
    add(f"- {guide['docs']['swagger_ui']}")
    add("")
    return "\n".join(lines)


def _json_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def register_guide(app: FastAPI) -> None:
    """挂上 GET /api 与 GET /api/（都免鉴权）。"""

    router = APIRouter()

    @router.get("/api", summary="API 使用指引（免鉴权）", tags=["guide"])
    @router.get("/api/", include_in_schema=False)
    async def api_guide(request: Request):
        guide = build_guide(app, request)
        accept = request.headers.get("accept", "")
        if "text/markdown" in accept:
            return HTMLResponse(content=render_markdown(guide), media_type="text/markdown")
        return JSONResponse(content=guide)

    app.include_router(router)
