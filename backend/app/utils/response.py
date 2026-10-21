from fastapi.responses import JSONResponse
from app.utils.status_code import StatusCode
from pydantic import BaseModel
from typing import Optional, Any


def http_status_for_code(code: int) -> int:
    """业务错误码 → HTTP 状态码。

    错误码分段约定（见 app/utils/status_code.py）：
      - 0        成功
      - 1000+    业务错误码（1xxx 任务阶段 / 2xxx 参数与供应商 / 3xxx 笔记 / 500000 系统异常）
      - 100~599  直接当作 HTTP 状态码使用

    业务错误码一律归为 400（调用方给错了参数或引用了不存在的资源），
    500000 归为 500，其余落在 HTTP 状态码区间的直接透传。
    这样「HTTP 状态码」和「body 里的 code」不再互相矛盾。
    """
    if code == 0:
        return 200
    if code >= 1000:
        return 500 if code >= 500000 else 400
    if 100 <= code <= 599:
        return code
    return 500


class ResponseWrapper:
    """统一响应包装：{code, msg, data}。

    全站只有这一种响应体形状 —— 成功、业务失败、HTTP 异常、参数校验失败
    都是它。客户端判断成败用 HTTP 状态码，判断业务细分用 body 里的 code。
    """

    @staticmethod
    def success(data=None, msg="success", code=0, status_code=200):
        return JSONResponse(
            status_code=status_code,
            content={
                "code": code,
                "msg": msg,
                "data": data,
            },
        )

    @staticmethod
    def error(msg="error", code=500, data=None, status_code=None):
        if status_code is None:
            status_code = http_status_for_code(code)
        return JSONResponse(
            status_code=status_code,
            content={
                "code": code,
                "msg": str(msg),
                "data": data,
            },
        )
