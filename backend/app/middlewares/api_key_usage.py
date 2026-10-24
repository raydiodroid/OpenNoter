"""API Key 用量日志中间件。

鉴权依赖（`get_current_user`）会把命中的 ApiKey 挂到 `request.state.api_key`，
这里在响应阶段读出来，记一条调用明细。

只在**用 API Key** 时记；网页登录态不记 —— 否则用量表会被自己的浏览器
点击淹没，失去「这把 key 到底被谁用」的意义。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.api_key_runtime import usage_logger
from app.utils.ip_allow import client_ip


class ApiKeyUsageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        response = await call_next(request)

        # 这里只能读标量：鉴权依赖的 DB session 在响应返回前已经关闭，
        # 此刻再去读 ORM 实例的属性会抛 DetachedInstanceError，把本该
        # 成功的响应变成 500。统计失败绝不能影响业务响应。
        key_id = getattr(request.state, "api_key_id", None)
        if key_id is not None:
            try:
                usage_logger.record(
                    key_id=key_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    ip=client_ip(request),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                pass

        return response
