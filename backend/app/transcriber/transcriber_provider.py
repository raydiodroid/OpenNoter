from app.transcriber.whisper import WhisperTranscriber
from app.utils.logger import get_logger
logger = get_logger(__name__)

logger.info('实例化transcriber')
# TODO:后面需要加入逻辑选择
_transcriber = None

def get_transcriber(model_size="base", device="cuda"):
    global _transcriber

    if _transcriber is None:
        logger.info('不存在 transcriber ，开始实例化transcriber。')
        try:
            _transcriber = WhisperTranscriber(model_size=model_size, device=device)
            logger.info(f'实例化transcriber成功。参数：{model_size}, {device} ')
        except Exception as e:
            logger.error(f"实例化transcriber失败，请检查是否安装whisper。{e}")
    return _transcriber