# exceptions.py
from app.enmus.exception import ProviderErrorEnum


class ProviderError(Exception):
    def __init__(self, message: str, code) -> None:
        super().__init__(message)
        # code 允许传枚举成员或裸 int，统一存成 int。
        # 之前这里原样存枚举成员，异常处理器把它塞进 JSON 响应体时会抛
        # TypeError（枚举不可 JSON 序列化），结果所有 ProviderError 都退化成
        # 500「系统异常」，调用方看不到真正的原因。
        self.code = getattr(code, "code", code)
        self.message = message
