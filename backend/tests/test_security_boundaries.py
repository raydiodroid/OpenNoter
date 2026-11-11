"""Account, ownership and outbound security regressions."""
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if k == 'app' or k.startswith('app.')}
    for k in saved: sys.modules.pop(k, None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "security.db"}')
    monkeypatch.setenv('OPENNOTER_JWT_SECRET', 'isolated-test-signing-key-at-least-32-bytes')
    monkeypatch.setenv('OPENNOTER_AUTH_ENFORCE', 'true')
    monkeypatch.setenv('OIDC_ISSUER', 'https://identity.example/issuer/')
    from app.db.init_db import init_db
    init_db()
    yield tmp_path
    for k in list(sys.modules):
        if k == 'app' or k.startswith('app.'): sys.modules.pop(k, None)
    sys.modules.update(saved)

def request(state):
    return Request({'type': 'http', 'headers': [(b'cookie', f'opennoter_oidc_state={state}'.encode())]})

def test_oidc_browser_binding_and_expiry(isolated):
    from app.routers import auth
    auth._OIDC_STATES['s'] = (time.time(), 'n', 'v')
    with pytest.raises(HTTPException) as e: auth.oidc_callback(request('other'), code='code', state='s')
    assert e.value.status_code == 400 and 's' in auth._OIDC_STATES
    auth._OIDC_STATES['s'] = (time.time()-601, 'n', 'v')
    with pytest.raises(HTTPException) as e: auth.oidc_callback(request('s'), code='code', state='s')
    assert e.value.status_code == 400 and 's' not in auth._OIDC_STATES

@pytest.mark.parametrize('verified,bound,expected', [(False, None, 403), (True, 'other', 403), (True, None, 302)])
def test_verified_email_and_existing_binding(isolated, monkeypatch, verified, bound, expected):
    from app.routers import auth
    from app.db.engine import SessionLocal
    from app.db.models.users import User
    monkeypatch.setattr(auth.oidc, 'exchange_code', lambda code, **kw: {'sub': 'new', 'email': 'admin@example.com', 'email_verified': verified})
    with SessionLocal() as db:
        user=User(email='admin@example.com', is_active=True, is_superuser=True, oidc_sub=bound, oidc_issuer='https://old.example' if bound else None)
        db.add(user); db.commit()
        auth._OIDC_STATES['s']=(time.time(), 'n', 'v')
        if expected==403:
            with pytest.raises(HTTPException) as e: auth.oidc_callback(request('s'), code='c', state='s', db=db)
            assert e.value.status_code==403
            db.refresh(user); assert user.oidc_sub==bound
        else:
            assert auth.oidc_callback(request('s'), code='c', state='s', db=db).status_code==302
            assert user.oidc_sub=='new'

def test_authorize_nonce_pkce_and_cookie(isolated, monkeypatch):
    from app.routers import auth
    from urllib.parse import urlsplit, parse_qs
    monkeypatch.setattr(auth.oidc, 'oidc_enabled', lambda: True)
    monkeypatch.setattr(auth.oidc, 'discovery', lambda: {'authorization_endpoint':'https://identity.example/auth'})
    response=auth.oidc_login(); query=parse_qs(urlsplit(response.headers['location']).query)
    assert query['nonce'] and query['code_challenge_method']==['S256']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    assert query['state'][0] in auth._OIDC_STATES

def test_proxy_admin_only(isolated):
    from app.routers import config
    from app.services.auth import get_optional_user, require_admin
    app=FastAPI(); app.include_router(config.router)
    app.dependency_overrides[get_optional_user]=lambda: SimpleNamespace(id=1,is_superuser=False)
    def deny(): raise HTTPException(status_code=403)
    app.dependency_overrides[require_admin]=deny
    assert TestClient(app).get('/proxy_config').status_code==403

def test_login_rate_limit(isolated):
    from app.routers import auth
    app=FastAPI(); app.include_router(auth.router); client=TestClient(app)
    for _ in range(10): assert client.post('/login',json={'email':'none@example.com','password':'bad'}).status_code==401
    assert client.post('/login',json={'email':'none@example.com','password':'bad'}).status_code==429

def test_unowned_and_corrupt_uploads(isolated):
    from app.utils.upload_paths import can_read_upload
    media=isolated/'clip.mp4'; media.write_bytes(b'media'); user=SimpleNamespace(id=1,is_superuser=False)
    assert not can_read_upload(media,user,auth_enforced=True)
    media.with_suffix('.mp4.owner').write_text('corrupt')
    assert not can_read_upload(media,user,auth_enforced=True)
    assert can_read_upload(media,SimpleNamespace(id=1,is_superuser=True),auth_enforced=True)

def test_owner_failure_aborts_upload(isolated,monkeypatch):
    from app.routers import note
    from app.services.auth import get_optional_user
    monkeypatch.setenv('UPLOAD_DIR',str(isolated/'uploads'))
    monkeypatch.setattr(note,'UPLOAD_DIR',str(isolated/'uploads'))
    def fail(*a): raise OSError('disk error')
    monkeypatch.setattr(note,'set_upload_owner',fail)
    app=FastAPI(); app.include_router(note.router)
    app.dependency_overrides[get_optional_user]=lambda: SimpleNamespace(id=1)
    response=TestClient(app).post('/upload',files={'file':('clip.mp4',b'contents','video/mp4')})
    assert response.status_code==500
    assert not list((isolated/'uploads').iterdir())

def test_single_resolution_and_ipv6(isolated,monkeypatch):
    from app.utils import url_safety as s
    dns=Mock(side_effect=[['93.184.216.34'],['127.0.0.1']]); monkeypatch.setattr(s,'resolve_host',dns)
    assert s.pin_url_to_ip('https://public.example:8443/path')==('https://93.184.216.34:8443/path','public.example:8443')
    assert dns.call_count==1
    with pytest.raises(s.UnsafeUrlError): s.pin_url_to_ip('https://public.example/path')
    monkeypatch.setattr(s,'resolve_host',lambda h:['2606:4700:4700::1111'])
    assert s.pin_url_to_ip('https://public.example/path')[0]=='https://[2606:4700:4700::1111]/path'

@pytest.mark.parametrize('ips',[[],['93.184.216.34','127.0.0.1'],['169.254.169.254'],['::1']])
def test_private_mixed_and_empty_dns_rejected(isolated,monkeypatch,ips):
    from app.utils import url_safety as s
    monkeypatch.setattr(s,'resolve_host',lambda h:ips)
    with pytest.raises(s.UnsafeUrlError): s.pin_url_to_ip('https://example.com/')

def test_cross_origin_redirect_strips_credentials(isolated,monkeypatch):
    from app.utils import url_safety as s
    monkeypatch.setattr(s,'resolve_host',lambda h:['93.184.216.34'])
    redirect=Mock(status_code=302,headers={'Location':'https://other.example/final'}); final=Mock(status_code=200)
    send=Mock(side_effect=[redirect,final]); monkeypatch.setattr(s,'_request_pinned',send)
    assert s.safe_get('https://first.example/start',suffixes=(),headers={'Authorization':'secret','Cookie':'secret','X-API-Key':'secret'},cookies={'secret':'yes'}) is final
    redirect.close.assert_called_once()
    assert send.call_args.kwargs['headers']=={'Host':'other.example'}
    assert 'cookies' not in send.call_args.kwargs

def test_requests_tls_identity(isolated,monkeypatch):
    from app.utils import url_safety as s
    import requests
    def send(session,prepared,**kwargs):
        pool=session.get_adapter(prepared.url).poolmanager.connection_pool_kw
        assert pool['server_hostname']==pool['assert_hostname']=='public.example'
        assert session.trust_env is False and kwargs['allow_redirects'] is False
        assert prepared.url=='https://93.184.216.34/test'
        return Mock()
    monkeypatch.setattr(requests.Session,'send',send)
    s._request_pinned('GET','https://93.184.216.34/test',hostname='public.example').close()

def test_byok_transport_tls_and_origin_boundary(isolated,monkeypatch):
    from app.utils import url_safety as s
    from app.utils.public_http import PublicEndpointTransport
    monkeypatch.setattr(s,'resolve_host',lambda h:['93.184.216.34'])
    transport=PublicEndpointTransport('https://model.example/v1'); seen=[]; transport.transport.close()
    transport.transport=httpx.MockTransport(lambda req:(seen.append(req) or httpx.Response(200)))
    with httpx.Client(transport=transport) as client:
        assert client.get('https://model.example/v1/models').status_code==200
        assert seen[0].url.host=='93.184.216.34' and seen[0].headers['Host']=='model.example'
        assert seen[0].extensions['sni_hostname']=='model.example'
        with pytest.raises(s.UnsafeUrlError): client.get('https://other.example/')
        monkeypatch.setattr(s,'resolve_host',lambda h:['127.0.0.1'])
        with pytest.raises(s.UnsafeUrlError): client.get('https://model.example/v1/models')

def test_admin_private_probe_does_not_relax_byok(isolated,monkeypatch):
    from app.routers.me_credentials import _validate_base_url
    monkeypatch.setenv('OPENNOTER_PROBE_ALLOW_PRIVATE','true')
    with pytest.raises(HTTPException) as e: _validate_base_url('http://127.0.0.1:8080/v1')
    assert e.value.status_code==400

@pytest.mark.parametrize('media_type,large,status', [('text/html',False,415),('image/svg+xml',False,415),('image/png',True,413),('image/png',False,200)])
def test_image_proxy_type_and_size_boundary(isolated,monkeypatch,media_type,large,status):
    from app.routers import note
    from app.services.auth import get_optional_user
    response=Mock(status_code=200,headers={'Content-Type':media_type})
    response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
    response.iter_content.return_value=iter([b'x'*(11*1024*1024) if large else b'png'])
    monkeypatch.setattr(note,'safe_get',lambda *a,**kw:response)
    app=FastAPI(); app.include_router(note.router)
    app.dependency_overrides[get_optional_user]=lambda:SimpleNamespace(id=1)
    result=TestClient(app).get('/image_proxy',params={'url':'https://i.ytimg.com/picture.png'})
    assert result.status_code==status
    if status==200: assert result.headers['x-content-type-options']=='nosniff'

@pytest.mark.parametrize('change', [{'nonce':'wrong'},{'exp':None},{'azp':'other'},{'iss':'https://other.example'}])
def test_oidc_token_claims_are_required_and_bound(isolated,monkeypatch,change):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from app.services import oidc
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    claims={'sub':'subject','email':'user@example.com','iss':'https://identity.example/issuer/', 'aud':'client', 'iat':int(time.time()),'exp':int(time.time())+60,'nonce':'nonce'}
    claims.update(change)
    claims={k:v for k,v in claims.items() if v is not None}
    token=jwt.encode(claims,key,algorithm='RS256')
    conf={'issuer':'https://identity.example/issuer/','jwks_uri':'https://identity.example/keys','token_endpoint':'https://identity.example/token'}
    monkeypatch.setenv('OIDC_CLIENT_ID','client')
    monkeypatch.setattr(oidc,'discovery',lambda:conf)
    monkeypatch.setattr(oidc,'_JWKS_CLIENT',SimpleNamespace(get_signing_key_from_jwt=lambda t:SimpleNamespace(key=key.public_key())))
    monkeypatch.setattr(oidc,'_JWKS_URI',conf['jwks_uri'])
    response=Mock(); response.json.return_value={'id_token':token}
    monkeypatch.setattr(oidc.httpx,'post',lambda *a,**kw:response)
    with pytest.raises((ValueError,jwt.PyJWTError)): oidc.exchange_code('code',nonce='nonce',verifier='verifier')
