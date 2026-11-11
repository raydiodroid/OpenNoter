"""用户自填 API 地址的 HTTPX 传输边界：校验并固定 IP，保留 TLS 身份。"""

import httpx

from app.utils.url_safety import UnsafeUrlError, pin_url_to_ip


class PublicEndpointTransport(httpx.BaseTransport):
    def __init__(self, base_url: str):
        self.base_url = base_url
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
        pinned, host = pin_url_to_ip(str(request.url), suffixes=())
        headers = request.headers.copy()
        headers["Host"] = host
        forwarded = httpx.Request(
            request.method, pinned, headers=headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        return self.transport.handle_request(forwarded)

    def close(self):
        self.transport.close()
