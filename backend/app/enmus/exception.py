import enum


class ProviderErrorEnum(enum.Enum):
    CONNECTION_TEST_FAILED = (200101, "供应商连接测试失败")
    SAVE_FAILED = (200102, "供应商保存失败")
    CREATE_FAILED = (200103, "供应商创建失败")
    NOT_FOUND = (200104, "供应商不存在/未保存")
    WRONG_PARAMETER = (200105, "API / API 地址不正确")
    UNKNOW_ERROR = (200106, "未知错误")

    def __init__(self, code, message):
        self.code = code
        self.message = message

class NoteErrorEnum(enum.Enum):
    PLATFORM_NOT_SUPPORTED = (300101 ,"选择的平台不受支持")
    MODEL_NOT_FOUND = (300102, "模型不存在或未启用")
    NO_AVAILABLE_MODEL = (300103, "没有可用的模型，请先在设置中启用供应商与模型")
    TRANSCRIBER_NOT_READY = (300104, "转写模型未就绪")

    def __init__(self, code, message):
        self.code = code
        self.message = message