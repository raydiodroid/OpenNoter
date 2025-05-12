# app/core/exception_handlers.py
from fastapi import Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper
from app.utils.status_code import StatusCode
logger = get_logger(__name__)

def register_exception_handlers(app):
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            loc = err.get("loc", [])
            field = loc[-1] if loc else "body"
            msg = err.get("msg", "参数不合法")
            errors.append({"field": field, "error": msg})
        return JSONResponse(
            status_code=400,
            content=ResponseWrapper.error(msg="参数验证失败", code=StatusCode.PARAM_ERROR, data=errors)
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ResponseWrapper.error(msg=str(exc.detail), code=StatusCode.FAIL)
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(f"服务器内部错误: {exc}")
        return JSONResponse(
            status_code=500,
            content=ResponseWrapper.error(msg="服务器内部错误", code=StatusCode.FAIL, data=str(exc))
        )
