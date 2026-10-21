# middlewares/exception_handler.py

from fastapi import Request
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.exceptions.biz_exception import BizException
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R
import traceback

logger = get_logger(__name__)

def register_exception_handlers(app: FastAPI):
    """把所有异常收敛成同一种响应体 {code, msg, data}。

    之前 FastAPI 自带的 HTTPException / RequestValidationError 会绕过这里，
    直接吐 {detail: ...}，客户端要同时兼容两套形状才能判错。现在统一了。
    """

    @app.exception_handler(BizException)
    async def biz_exception_handler(request: Request, exc: BizException):
        logger.error(f"BizException: {exc.code} - {exc.message}")
        return R.error(code=exc.code, msg=str(exc.message))

    @app.exception_handler(NoteError)
    async def note_exception_handler(request: Request, exc: NoteError):
        logger.error(f"NoteError: {exc.code} - {exc.message}")
        return R.error(code=exc.code, msg=str(exc.message))

    @app.exception_handler(ProviderError)
    async def provider_exception_handler(request: Request, exc: ProviderError):
        logger.error(f"供应商模块错误: {exc.code} - {exc.message}")
        # 「供应商不存在」是引用了不存在的资源，语义上应该是 404 而不是 400
        status = 404 if exc.code == ProviderErrorEnum.NOT_FOUND.code else None
        return R.error(code=exc.code, msg=str(exc.message), status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """收口 FastAPI/Starlette 的 HTTPException（含 404 路由不存在、401 未登录等）。"""
        detail = exc.detail
        data = None
        if isinstance(detail, dict):
            data = detail.get("data")
            msg = detail.get("msg") or detail.get("detail") or "请求失败"
        else:
            msg = str(detail)
        return R.error(code=exc.status_code, msg=msg, data=data, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """参数校验失败：422 + 具体的字段错误列表。"""
        return R.error(
            code=422,
            msg="参数校验失败",
            data={"errors": jsonable_encoder(exc.errors())},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error(f"系统异常: {str(exc)}\n{traceback.format_exc()}")
        return R.error(code=500000, msg="系统异常", status_code=500)
