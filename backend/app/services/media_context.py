"""Credentials already resolved by the trusted API, scoped to this disposable process."""
import os

_platform = None
_cookie = None
_proxy = None


def configure(platform, cookie, proxy):
    global _platform, _cookie, _proxy
    if not isolated():
        raise RuntimeError('Media context is only available inside a worker')
    _platform, _cookie, _proxy = platform, cookie, proxy


def isolated():
    return os.getenv('OPENNOTER_MEDIA_CHILD') == '1'


def cookie_for(platform):
    if platform != _platform:
        raise RuntimeError('Credential scope mismatch')
    return _cookie


def proxy_url():
    return _proxy
