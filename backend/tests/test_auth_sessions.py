import sys
from datetime import datetime, timedelta
import pytest
from fastapi import HTTPException
from starlette.requests import Request


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if k == 'app' or k.startswith('app.')}
    for key in saved:
        sys.modules.pop(key, None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "auth.db"}')
    monkeypatch.setenv('OPENNOTER_JWT_SECRET', 'test-session-secret-at-least-32-bytes')
    from app.db.init_db import init_db
    from app.db.engine import SessionLocal
    from app.db.models.users import User
    from app.services import auth
    init_db()
    with SessionLocal() as db:
        user = User(email='sessions@example.com', hashed_password=auth.hash_password('old-password'))
        db.add(user)
        db.commit()
        yield db, user, auth, SessionLocal
    for key in list(sys.modules):
        if key == 'app' or key.startswith('app.'):
            sys.modules.pop(key, None)
    sys.modules.update(saved)


def request(token, cookie=False):
    header = (b'cookie', f'opennoter_token={token}'.encode()) if cookie else (b'authorization', f'Bearer {token}'.encode())
    return Request({'type': 'http', 'headers': [header]})


def issue(db, user, auth):
    token = auth.create_access_token(user, db)
    db.commit()
    return token


def test_login_commits_a_usable_session(sessions):
    import json
    db, user, auth, _ = sessions
    from app.routers.auth import login, LoginRequest
    response = login(LoginRequest(email=user.email, password='old-password'),
                     Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1234)}), db)
    token = json.loads(response.body)['data']['token']
    assert auth.get_current_user(request(token), db).id == user.id
    assert 'HttpOnly' in response.headers['set-cookie']


@pytest.mark.parametrize('cookie', [False, True])
def test_logout_revokes_only_current_session(sessions, cookie):
    db, user, auth, _ = sessions
    from app.routers.auth import logout
    first, second = issue(db, user, auth), issue(db, user, auth)
    assert auth.get_current_user(request(first, cookie), db).id == user.id
    response = logout(request(first, cookie), db)
    assert 'opennoter_token=""' in response.headers['set-cookie']
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(request(first), db)
    assert error.value.status_code == 401
    assert auth.get_current_user(request(second), db).id == user.id
    assert logout(request(first), db).status_code == 200


def test_password_change_revokes_all_sessions_and_stale_concurrent_issuance(sessions):
    db, user, auth, SessionLocal = sessions
    from app.routers.auth import set_password
    from app.db.models.users import User
    first, second = issue(db, user, auth), issue(db, user, auth)
    with SessionLocal() as concurrent:
        stale = concurrent.get(User, user.id)
        assert stale.auth_version == 0
        set_password({'password': 'new-password', 'current_password': 'old-password'}, user, db)
        late = issue(concurrent, stale, auth)
    for token in [first, second, late]:
        with pytest.raises(HTTPException) as error:
            auth.get_current_user(request(token), db)
        assert error.value.status_code == 401
    db.refresh(user)
    assert auth.get_current_user(request(issue(db, user, auth)), db).id == user.id


def test_legacy_unknown_and_expired_sessions_are_rejected(sessions):
    db, user, auth, _ = sessions
    import jwt
    from app.db.models.auth_sessions import AuthSession
    token = issue(db, user, auth)
    payload = auth.decode_access_token(token)
    for missing in ['jti', 'ver', 'sub', 'exp', 'iat']:
        legacy = {k: v for k, v in payload.items() if k != missing}
        assert auth.decode_access_token(jwt.encode(legacy, auth._jwt_secret(), algorithm='HS256')) is None
    row = db.query(AuthSession).first()
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException):
        auth.get_current_user(request(token), db)
    db.delete(row)
    db.commit()
    with pytest.raises(HTTPException):
        auth.get_current_user(request(token), db)


def test_disable_then_reenable_does_not_resurrect_sessions(sessions):
    db, user, auth, _ = sessions
    from app.routers.auth import admin_update_user
    from app.db.models.users import User
    admin = User(email='admin@example.com', is_superuser=True)
    db.add(admin)
    db.commit()
    token = issue(db, user, auth)
    admin_update_user(user.id, {'is_active': False}, admin, db)
    admin_update_user(user.id, {'is_active': True}, admin, db)
    with pytest.raises(HTTPException):
        auth.get_current_user(request(token), db)
