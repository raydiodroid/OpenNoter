"""用户自填 API 地址的 HTTPX 传输边界：校验并固定 IP，保留 TLS 身份。"""

import httpx

from app.utils.url_safety import UnsafeUrlError, pin_url_to_ip, _request_pinned


class ProxyResponseStream(httpx.SyncByteStream):
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        from urllib3.exceptions import HTTPError
        try:
            # HTTPX 自己负责解压，不能在 requests 层重复解压。
            yield from self.response.raw.stream(65536, decode_content=False)
        except HTTPError as exc:
            raise httpx.ReadError("代理响应读取失败") from exc

    def close(self):
        self.response.close()


class PublicEndpointTransport(httpx.BaseTransport):
    def __init__(self, base_url: str, *, proxy_url=None):
        self.base_url = base_url
        self.proxy_url = proxy_url
        self.origin = self._origin(httpx.URL(base_url))
        self.transport = httpx.HTTPTransport(trust_env=False)

    @staticmethod
    def _origin(url):
        return url.scheme, url.host, url.port

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._origin(request.url) != self.origin:
            raise UnsafeUrlError("个人 API 凭据不能发送到其他来源")
        from app.services.byok_config import byok_config
        byok_config.check_base_url(self.base_url)
        pinned, host = pin_url_to_ip(str(request.url), suffixes=(), proxy_url=self.proxy_url)
        headers = request.headers.copy()
        headers["Host"] = host
        if self.proxy_url:
            # httpcore 1.0 的 CONNECT 隧道忽略 sni_hostname 扩展；复用支持
            # 原始域名证书校验的 requests 适配器，代理仍只连接验证后的 IP。
            import requests
            timeouts = request.extensions.get("timeout", {})
            try:
                response = _request_pinned(
                    request.method, pinned, hostname=request.url.raw_host.decode("ascii"),
                    proxy_url=self.proxy_url, headers=dict(headers), data=request.read(),
                    stream=True, timeout=(timeouts.get("connect", 30), timeouts.get("read", 600)),
                )
            except requests.RequestException as exc:
                raise httpx.ConnectError("无法连接代理目标", request=request) from exc
            return httpx.Response(response.status_code, headers=list(response.raw.headers.items()),
                                  stream=ProxyResponseStream(response))
        forwarded = httpx.Request(
            request.method, pinned, headers=headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        return self.transport.handle_request(forwarded)

    def close(self):
        self.transport.close()
