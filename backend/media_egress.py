"""Public-only HTTP proxy for isolated media containers.

The upstream proxy credential stays here. Workers get a revocable, short-lived
ticket. Every CONNECT/HTTP destination is resolved, checked and pinned before
the upstream proxy sees it, including when the upstream itself is on a LAN.
"""
import base64
import http.server
import json
import os
from pathlib import Path
import secrets
import select
import socket
import socketserver
import ssl
import threading
import time
from urllib.parse import unquote, urlsplit

from app.utils.url_safety import pin_url_to_ip

LEASES = {}
LOCK = threading.Lock()
MAX_BYTES = 1024 * 1024 * 1024


def lease(token):
    with LOCK:
        item = LEASES.get(token)
        return item if item and item['expires'] > time.monotonic() else None


def target_socket(host, port, upstream):
    if not host or not 1 <= port <= 65535:
        raise ValueError('Invalid destination')
    authority = f'[{host}]' if ':' in host else host
    pinned, _ = pin_url_to_ip(f'http://{authority}:{port}/', proxy_url=upstream)
    ip = urlsplit(pinned).hostname
    if not upstream:
        return socket.create_connection((ip, port), timeout=20)
    proxy = urlsplit(upstream)
    if proxy.scheme not in {'http', 'https'}:
        raise ValueError('Media egress requires an HTTP(S) upstream proxy')
    connection = socket.create_connection((proxy.hostname, proxy.port or (443 if proxy.scheme == 'https' else 80)), timeout=20)
    try:
        if proxy.scheme == 'https':
            connection = ssl.create_default_context().wrap_socket(connection, server_hostname=proxy.hostname)
        address = f'[{ip}]:{port}' if ':' in ip else f'{ip}:{port}'
        headers = f'CONNECT {address} HTTP/1.1\r\nHost: {address}\r\n'
        if proxy.username is not None:
            auth = base64.b64encode(f'{unquote(proxy.username)}:{unquote(proxy.password or "")}'.encode()).decode()
            headers += f'Proxy-Authorization: Basic {auth}\r\n'
        connection.sendall((headers + '\r\n').encode())
        # Read exactly the header so no tunneled bytes get swallowed.
        response = bytearray()
        while not response.endswith(b'\r\n\r\n') and len(response) < 16384:
            chunk = connection.recv(1)
            if not chunk:
                break
            response.extend(chunk)
        if not response.endswith(b'\r\n\r\n') or response.split(b' ', 2)[1:2] != [b'200']:
            raise OSError('Upstream proxy rejected connection')
        return connection
    except Exception:
        connection.close()
        raise


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass


class Control(QuietHandler):
    def do_POST(self):
        self.connection.settimeout(10)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/leases' or not 0 < size <= 16384:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            upstream = payload.get('upstream_proxy')
            if upstream and urlsplit(upstream).scheme not in {'http', 'https'}:
                raise ValueError()
            token = secrets.token_urlsafe(32)
            with LOCK:
                for old in list(LEASES):
                    if LEASES[old]['expires'] <= time.monotonic():
                        LEASES.pop(old)
                if len(LEASES) >= 256:
                    raise ValueError()
                LEASES[token] = {'upstream': upstream, 'expires': time.monotonic() + min(3660, max(1, int(payload['ttl'])))}
            body = json.dumps({'token': token}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(400, 'Invalid proxy lease')

    def do_DELETE(self):
        with LOCK:
            LEASES.pop(self.path.removeprefix('/leases/'), None)
        self.send_response(204)
        self.end_headers()


class Proxy(QuietHandler):
    timeout = 30

    def ticket(self):
        value = self.headers.get('Proxy-Authorization', '')
        if not value.startswith('Basic '):
            raise ValueError()
        token = base64.b64decode(value[6:], validate=True).decode().split(':', 1)[0]
        item = lease(token)
        if item is None:
            raise ValueError()
        return token, item

    def relay(self, upstream, token):
        transferred = 0
        while lease(token) and transferred < MAX_BYTES:
            readable, _, _ = select.select([self.connection, upstream], [], [], 2)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                transferred += len(data)
                (upstream if source is self.connection else self.connection).sendall(data)

    def do_CONNECT(self):
        self.close_connection = True
        started = False
        try:
            token, item = self.ticket()
            target = urlsplit('//' + self.path)
            if target.username or target.path or not target.port:
                raise ValueError()
            with target_socket(target.hostname, target.port, item['upstream']) as upstream:
                self.send_response(200, 'Connection established')
                self.end_headers()
                started = True
                self.relay(upstream, token)
        except Exception:
            if not started:
                self.send_error(403, 'Proxy destination unavailable')

    def forward(self):
        self.close_connection = True
        started = False
        try:
            token, item = self.ticket()
            target = urlsplit(self.path)
            if target.scheme != 'http' or target.username or target.fragment or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= MAX_BYTES or len(self.headers.get_all('Content-Length', [])) > 1:
                raise ValueError()
            with target_socket(target.hostname, target.port or 80, item['upstream']) as upstream:
                path = target.path or '/'
                if target.query:
                    path += '?' + target.query
                head = f'{self.command} {path} HTTP/1.1\r\nHost: {target.netloc}\r\nConnection: close\r\n'
                for key, value in self.headers.items():
                    if key.lower() not in {'host', 'connection', 'proxy-authorization', 'proxy-connection', 'expect'}:
                        if '\r' in value or '\n' in value:
                            raise ValueError()
                        head += f'{key}: {value}\r\n'
                upstream.sendall((head + '\r\n').encode('latin-1'))
                while size:
                    chunk = self.rfile.read(min(size, 65536))
                    if not chunk:
                        raise OSError()
                    upstream.sendall(chunk)
                    size -= len(chunk)
                started = True
                self.relay(upstream, token)
        except Exception:
            if not started:
                self.send_error(403, 'Proxy destination unavailable')

    do_GET = do_POST = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward


class TCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(64)

    def verify_request(self, *_):
        return self.slots.acquire(blocking=False)

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


if __name__ == '__main__':
    control_path = '/control/egress.sock'
    Path(control_path).unlink(missing_ok=True)
    with UnixServer(control_path, Control) as control, TCPServer(('172.16.249.2', 3128), Proxy) as proxy:
        os.chmod(control_path, 0o600)
        threading.Thread(target=control.serve_forever, daemon=True).start()
        proxy.serve_forever()
