from fastapi import FastAPI

from .routers import note, provider, model, config, chat, auth, monitor, api_keys
from .openapi import register_api_docs
from .api_guide import register_guide


def create_app(lifespan) -> FastAPI:
    # FastAPI 自带的 /docs /redoc /openapi.json 关掉，改为在下方挂自定义的
    # 「需鉴权」版本：既保留纵深防御（匿名看不到接口结构），又让 API 调用方
    # 拿到标准 OpenAPI schema。
    app = FastAPI(
        title="OpenNoter",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(note.router, prefix="/api")
    app.include_router(provider.router, prefix="/api")
    app.include_router(model.router,prefix="/api")
    app.include_router(config.router,  prefix="/api")
    # 健康检查免认证，容器探针与前端存活判定依赖它
    app.include_router(config.public_router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(api_keys.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(monitor.router, prefix="/api/monitor")

    register_api_docs(app)
    # 免鉴权的使用指引。放在最后注册，保证端点清单能覆盖上面所有路由。
    register_guide(app)

    return app
