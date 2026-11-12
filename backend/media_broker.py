"""Small trusted launcher; no application imports, public port, or caller-supplied Docker flags.

Only this service has the Docker socket. API and media containers do not.
Credentials travel over a Unix socket and stdin, never Docker arguments or env.
"""
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import subprocess
import threading
import time
import uuid

ROOT = Path('/jobs')
SOCKET = '/control/media.sock'
VOLUME = os.environ.get('MEDIA_JOBS_VOLUME', 'opennoter-media-jobs')
NETWORK = os.environ.get('MEDIA_NETWORK', 'opennoter-media-internal')
IMAGE = os.environ.get('MEDIA_IMAGE', 'opennoter-media:local')
TIMEOUT = min(3600, max(30, int(os.environ.get('MEDIA_TIMEOUT_SECONDS', '1800'))))
SLOTS = threading.BoundedSemaphore(int(os.environ.get('MEDIA_CONCURRENCY', '2')))
ACTIVE = set()
LOCK = threading.Lock()
OPERATIONS = {'subtitles', 'download', 'video', 'transcribe', 'frames', 'screenshot', 'probe'}


def command(job_id, name, *, networked=True):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid workspace')
    directory = ROOT / job_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid workspace')
    return ['docker', 'run', '--rm', '-i', '--pull=never', '--name', name,
            '--label', 'opennoter.media=true', '--user', '10001:0',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=128', '--memory=2g', '--memory-swap=2g', '--cpus=2',
            '--ulimit', 'nofile=256:256', '--ulimit', 'fsize=2147483648:2147483648',
            '--network', NETWORK if networked else 'none', '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=256m,uid=10001,gid=10001',
            '--mount', f'type=volume,src={VOLUME},dst=/job,volume-subpath={job_id},volume-nocopy',
            '--mount', f'type=bind,src={os.environ["MEDIA_MODELS_PATH"]},dst=/app/models,readonly',
            '--workdir=/job', '--env', 'PYTHONPATH=/app', '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'HOME=/tmp', '--env', 'OPENNOTER_MEDIA_CHILD=1',
            IMAGE, 'python', '/app/media_runtime.py']


def execute(payload):
    job_id = payload.get('job_id', '')
    if payload.get('operation') not in OPERATIONS:
        raise ValueError('Unsupported media operation')
    name = 'opennoter-media-' + uuid.uuid4().hex
    networked = (payload['operation'] in {'subtitles', 'download', 'video'} and payload.get('platform') != 'local'
                 or payload['operation'] == 'transcribe' and bool(payload.get('credential') or payload.get('engine') in {'bcut', 'kuaishou'}))
    args = command(job_id, name, networked=networked)
    deadline = time.monotonic() + min(TIMEOUT, max(1, int(payload.get('timeout_seconds', TIMEOUT))))
    with LOCK:
        if job_id in ACTIVE:
            raise ValueError('Workspace is busy')
        ACTIVE.add(job_id)
    try:
        if not SLOTS.acquire(timeout=max(0, deadline - time.monotonic())):
            raise ValueError('Media workers are busy')
        try:
            # No container logs: third-party tools may include URLs/Cookies in errors.
            result = ROOT / job_id / 'result.json'
            result.unlink(missing_ok=True)
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.communicate(json.dumps(payload).encode(), timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise ValueError('Media operation timed out') from None
            finally:
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=False)
            if proc.returncode != 0:
                raise ValueError('Media container failed')
            # Broker does not parse or return worker output; API validates it after exit.
            return {'ok': True}
        finally:
            SLOTS.release()
    finally:
        with LOCK:
            ACTIVE.discard(job_id)


def cleanup(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid workspace')
    with LOCK:
        if job_id in ACTIVE:
            raise ValueError('Workspace is busy')
        path = ROOT / job_id
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    return {'ok': True}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.connection.settimeout(30)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.path not in {'/execute', '/cleanup'} or not 0 < length <= 1024 * 1024:
                raise ValueError('Invalid media request')
            payload = json.loads(self.rfile.read(length))
            body = json.dumps(cleanup(payload.get('job_id', '')) if self.path == '/cleanup' else execute(payload)).encode()
            status = 200
        except Exception:
            # Never echo payload or subprocess output.
            body, status = b'{"ok":false}', 503
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 16


if __name__ == '__main__':
    # Remove orphan containers after broker/API restarts, before accepting work.
    old = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=opennoter.media=true'], timeout=30).decode().split()
    if old:
        subprocess.run(['docker', 'rm', '-f', *old], stdout=subprocess.DEVNULL, check=True, timeout=60)
    for entry in ROOT.iterdir():
        if re.fullmatch(r'[a-f0-9]{32}', entry.name):
            cleanup(entry.name)
    Path(SOCKET).unlink(missing_ok=True)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o600)
        server.serve_forever()
