"""Trusted API-side adapters for untrusted media operations.

Only explicit operation DTOs cross the boundary. No user IDs, DB paths, LLM keys,
master keys, environment snapshots, or arbitrary Docker options are transmitted.
"""
from dataclasses import asdict
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import time
import uuid

from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment

MAX_RESULT = 64 * 1024 * 1024


def enabled():
    return bool(os.getenv('MEDIA_WORKER_SOCKET'))


class UnixConnection(http.client.HTTPConnection):
    socket_env = 'MEDIA_WORKER_SOCKET'

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(os.environ[self.socket_env])


class EgressConnection(UnixConnection):
    socket_env = 'MEDIA_EGRESS_SOCKET'


class EgressLease:
    def __init__(self, upstream, ttl):
        self.token = None
        connection = EgressConnection('localhost', timeout=15)
        try:
            connection.request('POST', '/leases', json.dumps({'upstream_proxy': upstream, 'ttl': ttl + 30}).encode(),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read(4097)
            if response.status != 200 or len(data) > 4096:
                raise RuntimeError('媒体出口代理不可用')
            token = json.loads(data)['token']
            if not isinstance(token, str) or not 32 <= len(token) <= 64 or not all(c.isalnum() or c in '_-' for c in token):
                raise ValueError('Invalid proxy ticket')
            self.token = token
        finally:
            connection.close()

    @property
    def url(self):
        return f'http://{self.token}:x@172.16.249.2:3128'

    def close(self):
        connection = EgressConnection('localhost', timeout=10)
        try:
            connection.request('DELETE', '/leases/' + self.token)
            connection.getresponse().read(4096)
        except (OSError, http.client.HTTPException):
            pass  # ticket still has a bounded expiry; its container has exited
        finally:
            connection.close()


def transcript_result(data):
    if data is None:
        return None
    if not isinstance(data.get('full_text'), str) or len(data['full_text']) > 4_000_000:
        raise ValueError('Invalid worker transcript')
    segments = data.get('segments', [])
    if not isinstance(segments, list) or len(segments) > 100_000:
        raise ValueError('Invalid worker segments')
    return TranscriptResult(language=data.get('language'), full_text=data['full_text'],
                            segments=[TranscriptSegment(**s) for s in segments])


class MediaWorkspace:
    def __init__(self, owner):
        self.owner = owner
        self.id = uuid.uuid4().hex
        self.root = Path(os.getenv('MEDIA_JOBS_DIR', '/media-jobs')) / self.id
        self.root.mkdir(mode=0o770, parents=True)
        self.root.chmod(0o770)
        self.local_url = None

    def call(self, operation, **payload):
        from app.services.proxy_config_manager import ProxyConfigManager
        from app.downloaders.limits import get_download_limits
        limits = get_download_limits()
        remaining = min(int(os.getenv('TASK_TIMEOUT_SECONDS', '1800')),
                        int(getattr(self.owner, 'deadline', time.monotonic() + 1800) - time.monotonic()))
        if remaining <= 0:
            raise TimeoutError('任务已超时，已停止提交媒体操作')
        networked = (operation in {'subtitles', 'download', 'video'} and payload.get('platform') != 'local'
                     or operation == 'transcribe' and bool(payload.get('credential') or payload.get('engine') in {'bcut', 'kuaishou'}))
        lease = EgressLease(ProxyConfigManager().get_proxy_url(), remaining) if networked else None
        proxy = lease.url if lease else None
        request = {'job_id': self.id, 'operation': operation, 'proxy': proxy, 'timeout_seconds': remaining,
                   'limits': {'DOWNLOAD_MAX_SIZE_MB': limits.max_size_mb,
                              'DOWNLOAD_MAX_DURATION_SECONDS': limits.max_duration_seconds,
                              'DOWNLOAD_RETRY_COUNT': limits.retry_count}, **payload}
        connection = UnixConnection('localhost', timeout=remaining + 60)
        try:
            connection.request('POST', '/execute', body=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200 or len(response.read(4097)) > 4096:
                raise RuntimeError('隔离媒体 worker 不可用或执行超时，请检查服务状态')
        finally:
            connection.close()
            if lease:
                lease.close()
        path = self.path('/job/result.json')
        with path.open('rb') as file:
            content = file.read(MAX_RESULT + 1)
        if len(content) > MAX_RESULT:
            raise ValueError('Worker result exceeds limit')
        result = json.loads(content)
        path.unlink()
        if not result.get('ok'):
            from app.downloaders.errors import SizeLimitExceeded, DurationLimitExceeded, RetryableDownloadError
            if result.get('error') == 'SizeLimitExceeded':
                raise SizeLimitExceeded('媒体超过体积上限')
            if result.get('error') == 'DurationLimitExceeded':
                raise DurationLimitExceeded('媒体超过时长上限')
            if result.get('error') == 'NetworkError':
                raise RetryableDownloadError('媒体 worker 网络请求失败')
            raise RuntimeError('隔离媒体处理失败，请检查来源或转写配置')
        return result.get('data')

    def path(self, worker_path):
        value = Path(worker_path)
        if not value.is_absolute() or not value.is_relative_to('/job'):
            raise ValueError('Invalid worker artifact path')
        relative = value.relative_to('/job')
        path = self.root / relative
        if '..' in relative.parts or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Worker artifact escaped workspace')
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Worker artifact is a symlink')
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Worker artifact is not a regular file')
        return path

    def remote_path(self, value):
        relative = Path(value).relative_to(self.root)
        # Check again before submitting a previously returned path.
        self.path(str(Path('/job') / relative))
        return str(Path('/job') / relative)

    def url(self, platform, url):
        if platform != 'local':
            return str(url)
        if self.local_url is None:
            from app.utils.upload_paths import resolve_under_uploads
            source = resolve_under_uploads(str(url))
            destination = self.root / 'uploads'
            destination.mkdir(mode=0o770)
            destination.chmod(0o770)
            target = destination / source.name
            shutil.copyfile(source, target)
            target.chmod(0o660)
            self.local_url = '/uploads/' + source.name
        return self.local_url

    def import_image(self, worker_path, destination):
        source = self.path(worker_path)
        if source.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'} or source.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Invalid worker image')
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / (uuid.uuid4().hex + source.suffix.lower())
        shutil.copyfile(source, target)
        return str(target)

    def frames(self, path, grid_size, interval):
        frames = self.call('frames', path=self.remote_path(path), grid_size=grid_size, interval=interval)
        if not isinstance(frames, list) or len(frames) > 1000 or any(
            not isinstance(frame, str) or not frame.startswith('data:image/jpeg;base64,') for frame in frames
        ):
            raise ValueError('Invalid worker frames')
        return frames

    def screenshot(self, path, destination, timestamp, index):
        result = self.call('screenshot', path=self.remote_path(path), timestamp=timestamp, index=index)
        return self.import_image(result, destination)

    def close(self):
        connection = UnixConnection('localhost', timeout=30)
        try:
            connection.request('POST', '/cleanup', json.dumps({'job_id': self.id}).encode(),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read(4096)
            if response.status != 200:
                raise RuntimeError('Worker cleanup failed')
        except Exception:
            # Broker also clears orphan workspaces at startup; shared tmpfs is bounded.
            shutil.rmtree(self.root, ignore_errors=True)
        finally:
            connection.close()


class WorkerDownloader:
    def __init__(self, workspace, platform, user_id):
        self.workspace, self.platform, self.user_id = workspace, platform, user_id

    def _call(self, operation, video_url, **options):
        from app.services.credential_resolver import resolve_cookie
        cookie = resolve_cookie(self.user_id, self.platform) if self.platform != 'local' else None
        return self.workspace.call(operation, platform=self.platform, cookie=cookie,
                                   url=self.workspace.url(self.platform, video_url), **options)

    def download_subtitles(self, video_url, **_):
        return transcript_result(self._call('subtitles', video_url))

    def download_video(self, video_url, **_):
        return str(self.workspace.path(self._call('video', video_url)))

    def download(self, video_url, quality='medium', need_video=False, skip_download=False, **_):
        data = self._call('download', video_url, quality=getattr(quality, 'value', quality),
                          need_video=bool(need_video), skip_download=bool(skip_download))
        for key in ('file_path', 'video_path'):
            if data.get(key):
                data[key] = str(self.workspace.path(data[key])) if not skip_download else ''
        cover = data.get('cover_url') or ''
        if cover.startswith('/static/'):
            from app.utils.static_signing import sign_static_url
            task_id = getattr(self.workspace.owner, 'media_task_id', None)
            if task_id:
                from app.utils.note_paths import validate_task_id
                validate_task_id(task_id)
                directory = Path(os.getenv('OUT_DIR', './static/screenshots')) / task_id
                prefix = os.getenv('IMAGE_BASE_URL', '/static/screenshots').rstrip('/') + '/' + task_id
            else:
                directory, prefix = Path('static/cover'), '/static/cover'
            imported = self.workspace.import_image('/job' + cover, directory)
            data['cover_url'] = sign_static_url(prefix + '/' + Path(imported).name)
        # Third-party metadata can contain cookies/headers or internal paths.
        data['raw_info'] = {}
        return AudioDownloadResult(**data)


class WorkerTranscriber:
    def __init__(self, workspace, owner):
        self.workspace, self.owner = workspace, owner

    def transcript(self, file_path):
        from app.services.credential_resolver import resolve_asr
        from app.services.asr_providers import AsrProviderService
        engine = self.owner.transcriber_type
        credential = resolve_asr(self.owner.user_id, online=engine == 'groq' or engine.startswith('asr_'),
                                 provider_id=engine, source=self.owner.transcriber_source)
        if credential is None:
            AsrProviderService.require_enabled(engine)
        model_size = self.owner.model_size
        if credential is None and engine == 'fast-whisper':
            from app.transcriber.model_readiness import _check_whisper_model_exists
            from app.transcriber.whisper_models import resolve_whisper_model
            if not _check_whisper_model_exists(model_size):
                raise RuntimeError('转写模型尚未下载就绪，请先在转写设置中下载')
            model_size = resolve_whisper_model(model_size)
        return transcript_result(self.workspace.call('transcribe', path=self.workspace.remote_path(file_path),
                                 engine=engine, model_size=model_size,
                                 credential=asdict(credential) if credential else None))
