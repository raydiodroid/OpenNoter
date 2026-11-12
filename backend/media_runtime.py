"""One media operation in a disposable container; no DB/config/master secrets mounted."""
import json
import os
from pathlib import Path
import sys
from dataclasses import asdict
from types import SimpleNamespace


def local_path(value):
    path = Path(value)
    if not path.is_absolute() or not path.resolve().is_relative_to('/job'):
        raise ValueError('Invalid media path')
    return str(path)


def run(payload):
    os.umask(0o007)
    os.environ.update(DATA_DIR='/job/data', UPLOAD_DIR='/job/uploads',
                      DATABASE_URL='sqlite:////tmp/unused.db', MEDIA_JOB_ROOT='/job',
                      HF_HOME='/tmp/huggingface', HF_HUB_OFFLINE='1')
    for key, value in payload.get('limits', {}).items():
        if key in {'DOWNLOAD_MAX_SIZE_MB', 'DOWNLOAD_MAX_DURATION_SECONDS', 'DOWNLOAD_RETRY_COUNT'}:
            os.environ[key] = str(int(value))
    # Explicit per-operation context. These functions cannot reach site configuration.
    from app.services.media_context import configure
    configure(payload.get('platform'), payload.get('cookie'), payload.get('proxy'))
    if payload.get('proxy'):
        from app.services.proxy_config_manager import ProxyConfigManager
        ProxyConfigManager().apply_to_env()
    operation = payload['operation']
    if operation == 'probe':
        return {'uid': os.getuid(), 'database': Path('/app/data/open_noter.db').exists(),
                'docker_socket': Path('/var/run/docker.sock').exists(),
                'jwt_secret': bool(os.getenv('OPENNOTER_JWT_SECRET') or os.getenv('BILINOTE_JWT_SECRET'))}
    if operation in {'subtitles', 'download', 'video'}:
        from app.services.constant import get_downloader
        downloader = get_downloader(payload['platform'])
        url = payload['url']
        if operation == 'subtitles':
            result = downloader.download_subtitles(url)
        elif operation == 'video':
            return downloader.download_video(url, output_dir='/job/data')
        else:
            result = downloader.download(url, output_dir='/job/data', quality=payload['quality'],
                                         need_video=payload['need_video'], skip_download=payload['skip_download'])
        return asdict(result) if result else None
    if operation == 'transcribe':
        credential = payload.get('credential')
        if credential:
            from app.transcriber.groq import GroqTranscriber
            transcriber = GroqTranscriber(credential=SimpleNamespace(**credential))
        elif payload['engine'] == 'bcut':
            from app.transcriber.bcut import BcutTranscriber
            transcriber = BcutTranscriber()
        elif payload['engine'] == 'kuaishou':
            from app.transcriber.kuaishou import KuaishouTranscriber
            transcriber = KuaishouTranscriber()
        elif payload['engine'] == 'fast-whisper':
            from app.transcriber.whisper import WhisperTranscriber
            transcriber = WhisperTranscriber(model_size=payload['model_size'], device='cpu')
        else:
            raise ValueError('Unsupported isolated transcriber')
        return asdict(transcriber.transcript(local_path(payload['path'])))
    if operation == 'frames':
        from app.utils.video_reader import VideoReader
        return VideoReader(local_path(payload['path']), grid_size=tuple(payload['grid_size']),
                           frame_interval=payload['interval'], unit_width=960, unit_height=540,
                           save_quality=80).run()
    if operation == 'screenshot':
        from app.utils.video_helper import generate_screenshot
        return generate_screenshot(local_path(payload['path']), '/job/screenshots', payload['timestamp'], payload['index'])
    raise ValueError('Unsupported operation')


if __name__ == '__main__':
    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
        result = {'ok': True, 'data': run(payload)}
    except Exception as exc:
        # Do not expose upstream URLs, headers, paths, or credential values.
        code = type(exc).__name__
        from app.downloaders.errors import is_retryable
        if is_retryable(exc):
            code = 'NetworkError'
        result = {'ok': False, 'error': code if code in {'SizeLimitExceeded', 'DurationLimitExceeded', 'NetworkError'} else 'MediaError'}
    Path('/job/result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
