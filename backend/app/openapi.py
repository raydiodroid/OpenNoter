"""鉴权后才可访问的 API 文档。

之前 FastAPI 自带的 /docs /openapi.json 被刻意关掉了（`docs_url=None`），
理由是「会完整暴露接口结构」。但这也让 API 调用方完全无从知道有哪些端点、
字段叫什么、有哪些可选值 —— 只能读源码或者反复试。

现在的取舍：**带凭据就能看，匿名一律 401**。
  - /openapi.json  返回标准 OpenAPI 3.1 schema，机器可读（curl / codegen / agent）
  - /docs          返回 Swagger UI，schema 直接内联进 HTML
                    （不用再发一次带凭据的请求，所以浏览器打开就能用）

内联而不是让 Swagger UI 自己 fetch，是为了让「浏览器已登录（带 cookie）」和
「只有 API Key」两种情况都能正常渲染 —— 前者靠 cookie，后者靠内联。
"""

import json

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from app.db.models.users import User
from app.services.auth import get_current_user

API_VERSION = "2.0.0"

# Swagger UI 静态资源。jsdelivr 在国内偶尔不稳，挂了自动回退 unpkg。
_SWAGGER_CSS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    "https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
)
_SWAGGER_JS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
    "https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
)


def build_openapi_schema(app: FastAPI) -> dict:
    """生成带鉴权声明的 OpenAPI schema。"""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=API_VERSION,
        description=(
            "OpenNoter 后端 API。所有响应统一为 `{code, msg, data}` 三段式：\n\n"
            "- `code` 为 0 表示成功；非 0 时 `msg` 是人类可读的失败原因\n"
            "- HTTP 状态码同时表达成败（4xx / 5xx），可直接据此判断，不要只看 code\n\n"
            "鉴权方式（任选其一）：\n\n"
            "- `x-api-key: sk-bili-...` 请求头\n"
            "- `Authorization: Bearer sk-bili-...` 请求头（API Key）\n"
            "- `Authorization: Bearer <JWT>` 请求头（网页登录态）\n"
            "- `Cookie: opennoter_token=<JWT>`（浏览器）"
        ),
        routes=app.routes,
    )

    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"},
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "API Key 或 JWT"},
    }
    schema["security"] = [{"ApiKeyAuth": []}, {"BearerAuth": []}]

    app.openapi_schema = schema
    return schema


def _swagger_ui_html(schema: dict) -> str:
    """把 OpenAPI schema 内联进 Swagger UI 页面。"""
    # </script> 出现在 JSON 里会提前闭合脚本标签，必须转义
    spec = json.dumps(schema, ensure_ascii=False, indent=2).replace("</", "<\\/")

    css_primary, css_fallback = _SWAGGER_CSS
    js_primary, js_fallback = _SWAGGER_JS

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenNoter API 文档</title>
  <link rel="stylesheet" href="{css_primary}"
        onerror="this.onerror=null;this.href='{css_fallback}';" />
  <style>
    body {{ margin: 0; background: #fafafa; }}
    .topbar {{ display: none; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{js_primary}"
          onerror="this.onerror=null;var s=document.createElement('script');s.src='{js_fallback}';s.onload=init;s.onerror=function(){{document.getElementById('swagger-ui').innerHTML='<p style="padding:24px;font-family:sans-serif">Swagger UI 静态资源加载失败，请直接访问 <code>/openapi.json</code> 获取原始 schema。</p>';}};document.head.appendChild(s);"></script>
  <script>
    const SPEC = {spec};
    function init() {{
      window.ui = SwaggerUIBundle({{
        spec: SPEC,
        dom_id: '#swagger-ui',
        deepLinking: true,
        persistAuthorization: true,
        presets: [SwaggerUIBundle.presets.apis],
        layout: 'BaseLayout',
        tryItOutEnabled: true,
      }});
    }}
    if (window.SwaggerUIBundle) {{ init(); }}
  </script>
</body>
</html>
"""


def register_api_docs(app: FastAPI) -> None:
    """挂上 /openapi.json 与 /docs（都要求有效凭据）。"""

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json(request: Request, user: User = Depends(get_current_user)):
        return JSONResponse(build_openapi_schema(app))

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui(request: Request, user: User = Depends(get_current_user)):
        return HTMLResponse(_swagger_ui_html(build_openapi_schema(app)))
