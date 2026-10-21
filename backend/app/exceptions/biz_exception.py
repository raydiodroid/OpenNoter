# exceptions/biz_exception.py

class BizException(Exception):
    def __init__(self, code: int, message: str = "业务异常"):
        super().__init__(message)
        # 同 ProviderError / NoteError：枚举成员统一转成 int
        self.code = getattr(code, "code", code)
        self.message = message