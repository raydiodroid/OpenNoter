import importlib.util
from pathlib import Path
from unittest.mock import Mock
import base64
import time
import pytest
from tests.test_auth_sessions import sessions  # noqa: F401


@pytest.fixture
def egress(sessions):
    spec = importlib.util.spec_from_file_location('egress_test', Path(__file__).parents[1] / 'media_egress.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '10.0.0.1', '192.168.144.2', '::1', '100.64.0.1'])
def test_private_destinations_blocked_even_through_upstream_proxy(egress, monkeypatch, host):
    connect = Mock()
    monkeypatch.setattr(egress.socket, 'create_connection', connect)
    with pytest.raises(ValueError):
        egress.target_socket(host, 80, 'http://upstream:7890')
    connect.assert_not_called()


def test_connect_uses_pinned_ip_and_only_gateway_knows_upstream_password(egress, monkeypatch):
    monkeypatch.setattr(egress, 'pin_url_to_ip', lambda *a, **kw: ('http://93.184.216.34:443/', 'public.example'))
    connection = Mock()
    connection.recv.side_effect = [bytes([n]) for n in b'HTTP/1.1 200 Connection established\r\n\r\n']
    connect = Mock(return_value=connection)
    monkeypatch.setattr(egress.socket, 'create_connection', connect)
    assert egress.target_socket('public.example', 443, 'http://proxy-user:secret@private-upstream:7890') is connection
    connect.assert_called_once_with(('private-upstream', 7890), timeout=20)
    head = connection.sendall.call_args.args[0]
    assert b'CONNECT 93.184.216.34:443 HTTP/1.1' in head
    assert base64.b64encode(b'proxy-user:secret') in head
    assert b'public.example' not in head  # upstream cannot re-resolve/rebind it


def test_expired_and_revoked_tickets_cannot_open_tunnel(egress):
    token = 'x'*43
    handler = object.__new__(egress.Proxy)
    handler.headers = {'Proxy-Authorization': 'Basic ' + base64.b64encode(f'{token}:x'.encode()).decode()}
    egress.LEASES[token] = {'expires': time.monotonic()+10, 'upstream': None}
    assert handler.ticket()[0] == token
    egress.LEASES[token]['expires'] = time.monotonic()-1
    with pytest.raises(ValueError):
        handler.ticket()
    egress.LEASES.pop(token)
    with pytest.raises(ValueError):
        handler.ticket()


def test_revocation_stops_existing_tunnel(egress):
    handler = object.__new__(egress.Proxy)
    handler.connection = Mock()
    upstream = Mock()
    handler.relay(upstream, 'revoked-ticket')
    upstream.recv.assert_not_called()
    handler.connection.recv.assert_not_called()
