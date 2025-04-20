import os
import platform

from app.transcriber.whisper import WhisperTranscriber
from app.transcriber.bcut import BcutTranscriber
from app.transcriber.kuaishou import KuaishouTranscriber
from app.utils.logger import get_logger
logger = get_logger(__name__)

# 只在Apple平台且设置了环境变量时才导入MLX Whisper
if platform.system() == "Darwin" and os.environ.get("TRANSCRIBER_TYPE") == "mlx-whisper":
    try:
        from app.transcriber.mlx_whisper_transcriber import MLXWhisperTranscriber
        MLX_WHISPER_AVAILABLE = True
        logger.info("MLX Whisper 可用，已导入")
    except ImportError:
        MLX_WHISPER_AVAILABLE = False
        logger.warning("MLX Whisper 导入失败，可能未安装或平台不支持")
else:
    MLX_WHISPER_AVAILABLE = False

logger.info('初始化转录服务提供器')

# 维护各种转录器的单例实例
_transcribers = {
    'whisper': None,
    'bcut': None,
    'kuaishou': None,
    'mlx-whisper': None
}

def get_whisper_transcriber(model_size="base", device="cuda"):
    """获取 Whisper 转录器实例"""
    if _transcribers['whisper'] is None:
        logger.info(f'创建 Whisper 转录器实例，参数：{model_size}, {device}')
        try:
            _transcribers['whisper'] = WhisperTranscriber(model_size=model_size, device=device)
            logger.info('Whisper 转录器创建成功')
        except Exception as e:
            logger.error(f"Whisper 转录器创建失败: {e}")
            raise
    return _transcribers['whisper']

def get_bcut_transcriber():
    """获取 Bcut 转录器实例"""
    if _transcribers['bcut'] is None:
        logger.info('创建 Bcut 转录器实例')
        try:
            _transcribers['bcut'] = BcutTranscriber()
            logger.info('Bcut 转录器创建成功')
        except Exception as e:
            logger.error(f"Bcut 转录器创建失败: {e}")
            raise
    return _transcribers['bcut']

def get_kuaishou_transcriber():
    """获取快手转录器实例"""
    if _transcribers['kuaishou'] is None:
        logger.info('创建快手转录器实例')
        try:
            _transcribers['kuaishou'] = KuaishouTranscriber()
            logger.info('快手转录器创建成功')
        except Exception as e:
            logger.error(f"快手转录器创建失败: {e}")
            raise
    return _transcribers['kuaishou']

def get_mlx_whisper_transcriber(model_size="base"):
    """获取 MLX Whisper 转录器实例"""
    if not MLX_WHISPER_AVAILABLE:
        logger.warning("MLX Whisper 不可用，请确保在Apple平台且已安装mlx_whisper")
        raise ImportError("MLX Whisper 不可用，请确保在Apple平台且已安装mlx_whisper")
        
    if _transcribers['mlx-whisper'] is None:
        logger.info(f'创建 MLX Whisper 转录器实例，参数：{model_size}')
        try:
            _transcribers['mlx-whisper'] = MLXWhisperTranscriber(model_size=model_size)
            logger.info('MLX Whisper 转录器创建成功')
        except Exception as e:
            logger.error(f"MLX Whisper 转录器创建失败: {e}")
            raise
    return _transcribers['mlx-whisper']

def get_transcriber(transcriber_type="fast-whisper", model_size="base", device="cuda"):
    """
    获取指定类型的转录器实例
    
    参数:
        transcriber_type: 转录器类型，支持 "fast-whisper", "bcut", "kuaishou", "mlx-whisper"(仅Apple平台)
        model_size: 模型大小，whisper 和 mlx-whisper 特有参数
        device: 设备类型，whisper 特有参数
    
    返回:
        对应类型的转录器实例
    """
    logger.info(f'获取转录器，类型: {transcriber_type}')
    if transcriber_type == "fast-whisper":
        whisper_model_size = os.environ.get("WHISPER_MODEL_SIZE",model_size)
        return get_whisper_transcriber(whisper_model_size, device=device)
    elif transcriber_type == "mlx-whisper":
        whisper_model_size = os.environ.get("WHISPER_MODEL_SIZE",model_size)
        if not MLX_WHISPER_AVAILABLE:
            logger.warning("MLX Whisper 不可用，回退到 fast-whisper")
            return get_whisper_transcriber(whisper_model_size, device=device)
        return get_mlx_whisper_transcriber(whisper_model_size)
    elif transcriber_type == "bcut":
        return get_bcut_transcriber()
    elif transcriber_type == "kuaishou":
        return get_kuaishou_transcriber()
    else:
        logger.warning(f'未知转录器类型 "{transcriber_type}"，使用默认 whisper')
        whisper_model_size = os.environ.get("WHISPER_MODEL_SIZE",model_size)
        return get_whisper_transcriber(whisper_model_size, device)