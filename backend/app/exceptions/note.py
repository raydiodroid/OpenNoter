# exceptions.py
from app.enmus.exception import ProviderErrorEnum


class NoteError(Exception):
    def __init__(self, message: str, code) -> None:
        super().__init__(message)
        # 同 ProviderError：枚举成员统一转成 int，否则进 JSON 响应体会抛 TypeError
        self.code = getattr(code, "code", code)
        self.message = message
