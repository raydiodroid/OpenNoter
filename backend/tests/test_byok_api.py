"""BYOK Step 2 的接口测试：用户凭据 CRUD + 管理员模式切换。

重点覆盖四类容易出错的行为：

1. **权限边界**：普通用户管不了别人的凭据，管理员才能切模式；
2. **``shared`` 模式下功能对用户不可见**（返回 404 而非 403）；
3. **掩码污染防御**：前端回填掩码后保存，不能把 ``***`` 写进库；
4. **响应体永不回传明文 key**。

用 FastAPI TestClient + 依赖覆盖（``get_current_user`` / ``require_admin``）构造
两个身份的客户端，避免走真实登录流程。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """返回 (client, H)：带依赖覆盖的 TestClient。

    pytest.ini 设了 ``pythonpath = .``，所以直接 import 即可，
    不需要像独立脚本那样手工构造 ``app`` 包命名空间。
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "unit-test-api-secret-0001")
    monkeypatch.setenv("OPENNOTER_JWT_SECRET", "unit-test-jwt-secret-0001")
    monkeypatch.chdir(tmp_path)

    from app.db.init_db import init_db

    init_db()

    # users 表的配额列由 migrate_task_status.py 用 ALTER TABLE 补，
    # create_all 不会加列。测试用临时库需要自己补齐，否则读取 User 会
    # OperationalError: no such column: users.max_concurrent_tasks
    # （这是既有项目的迁移约定，不是 BYOK 引入的。）
    from sqlalchemy import text

    from app.db.engine import get_engine

    with get_engine().begin() as conn:
        existing = {r[1] for r in conn.execute(text("PRAGMA table_info(users)"))}
        for col in ("max_concurrent_tasks", "daily_task_limit", "storage_quota_mb"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} INTEGER"))

    from fastapi.testclient import TestClient

    from app import create_app
    from app.db.engine import SessionLocal
    from app.db.models.users import User
    from app.services.auth import get_current_user, get_optional_user, require_admin

    db = SessionLocal()
    try:
        # 幂等：同一进程内多次建 fixture 时不重复插入
        for uid, email in ((1, "alice@example.com"), (2, "bob@example.com")):
            if db.query(User).filter(User.id == uid).first() is None:
                db.add(User(id=uid, email=email, is_superuser=False, is_active=True))
        db.commit()
    finally:
        db.close()

    app = create_app(lifespan=None)

    state = {"user_id": 1}

    def _fake_user():
        s = SessionLocal()
        try:
            return s.query(User).filter(User.id == state["user_id"]).first()
        finally:
            s.close()

    def _fake_admin():
        s = SessionLocal()
        try:
            return s.query(User).filter(User.id == 1).first()
        finally:
            s.close()

    app.dependency_overrides[get_current_user] = _fake_user
    # config.py / provider.py 的 router 级依赖是 get_optional_user，
    # 不覆盖它的话 /api/byok_config 会因「匿名」直接 401。
    app.dependency_overrides[get_optional_user] = _fake_user
    app.dependency_overrides[require_admin] = _fake_admin

    client = TestClient(app)

    class H:
        @staticmethod
        def as_user(uid: int):
            state["user_id"] = uid

    yield client, H


def _set_mode(mode: str):
    from app.services.byok_config import byok_config

    byok_config.update(mode=mode)


def _payload(**over):
    data = {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-user-secret-key",
        "model_name": "deepseek-chat",
    }
    data.update(over)
    return data


# ---------------------------------------------------------------- 管理员模式切换


class TestByokModeApi:
    def test_get_returns_current_mode(self, api):
        client, _ = api
        resp = client.get("/api/byok_config")
        assert resp.status_code == 200
        assert resp.json()["data"]["mode"] == "shared"  # 默认

    def test_admin_can_switch_mode(self, api):
        client, _ = api
        for mode in ("optional", "required", "shared"):
            resp = client.patch("/api/byok_config", json={"mode": mode})
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["mode"] == mode
            # 切换立即生效（再读一次）
            assert client.get("/api/byok_config").json()["data"]["mode"] == mode

    def test_invalid_mode_rejected(self, api):
        client, _ = api
        resp = client.patch("/api/byok_config", json={"mode": "bogus"})
        assert resp.status_code == 400

    def test_empty_body_rejected(self, api):
        client, _ = api
        assert client.patch("/api/byok_config", json={}).status_code == 400


# ---------------------------------------------------------------- 功能可见性


class TestVisibility:
    def test_shared_mode_hides_byok_api(self, api):
        """shared 模式下 BYOK 接口应 404（功能不存在），不是 403。"""
        client, H = api
        _set_mode("shared")
        H.as_user(1)

        assert client.get("/api/me/credentials").status_code == 200  # 读允许，返回空
        body = client.get("/api/me/credentials").json()["data"]
        assert body["mode"] == "shared"
        assert body["credentials"] == []

        # 写操作在 shared 模式下不存在
        assert client.put("/api/me/credentials/llm", json=_payload()).status_code == 404
        assert client.delete("/api/me/credentials/llm").status_code == 404

    def test_optional_mode_enables_crud(self, api):
        client, H = api
        _set_mode("optional")
        H.as_user(1)

        resp = client.put("/api/me/credentials/llm", json=_payload())
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["configured"] is True


# ---------------------------------------------------------------- CRUD


class TestCredentialCrud:
    def test_upsert_then_list(self, api):
        client, H = api
        _set_mode("optional")
        H.as_user(1)

        assert client.put("/api/me/credentials/llm", json=_payload()).status_code == 200

        data = client.get("/api/me/credentials").json()["data"]
        assert len(data["credentials"]) == 1
        item = data["credentials"][0]
        assert item["capability"] == "llm"
        assert item["model_name"] == "deepseek-chat"
        assert item["configured"] is True

    def test_response_never_contains_plaintext_key(self, api):
        """最关键的一条：任何响应体都不能出现明文 key。"""
        client, H = api
        _set_mode("optional")
        H.as_user(1)
        client.put("/api/me/credentials/llm", json=_payload())

        raw = client.get("/api/me/credentials").text
        assert "sk-user-secret-key" not in raw
        # 只允许掩码形式
        assert "***" in raw

    def test_upsert_is_idempotent(self, api):
        """PUT 语义：同一能力位重复保存应更新而非报唯一约束冲突。"""
        client, H = api
        _set_mode("optional")
        H.as_user(1)

        assert client.put("/api/me/credentials/llm", json=_payload()).status_code == 200
        assert (
            client.put(
                "/api/me/credentials/llm", json=_payload(model_name="deepseek-reasoner")
            ).status_code
            == 200
        )

        creds = client.get("/api/me/credentials").json()["data"]["credentials"]
        assert len(creds) == 1
        assert creds[0]["model_name"] == "deepseek-reasoner"

    def test_delete_restores_site_default(self, api):
        client, H = api
        _set_mode("optional")
        H.as_user(1)

        client.put("/api/me/credentials/llm", json=_payload())
        assert client.delete("/api/me/credentials/llm").status_code == 200
        assert client.get("/api/me/credentials").json()["data"]["credentials"] == []

    def test_users_are_isolated(self, api):
        """A 配的凭据 B 看不到。"""
        client, H = api
        _set_mode("optional")

        H.as_user(1)
        client.put("/api/me/credentials/llm", json=_payload())
        assert len(client.get("/api/me/credentials").json()["data"]["credentials"]) == 1

        H.as_user(2)
        assert client.get("/api/me/credentials").json()["data"]["credentials"] == []

    def test_masked_key_does_not_overwrite(self, api):
        """掩码污染防御：把回填的 ``***abcd`` 存回去，不能覆盖真 key。"""
        client, H = api
        _set_mode("optional")
        H.as_user(1)

        client.put("/api/me/credentials/llm", json=_payload())

        from app.db.engine import SessionLocal
        from app.db.models.user_credentials import UserCredential
        from app.utils import credential_crypto

        db = SessionLocal()
        try:
            before = db.query(UserCredential).filter(UserCredential.user_id == 1).first()
            sealed_before = before.api_key
        finally:
            db.close()

        # 前端拿到掩码后原样保存
        masked = client.get("/api/me/credentials").json()["data"]["credentials"][0]["masked_key"]
        resp = client.put("/api/me/credentials/llm", json=_payload(api_key=masked))
        assert resp.status_code == 200

        db = SessionLocal()
        try:
            after = db.query(UserCredential).filter(UserCredential.user_id == 1).first()
            # 密文未变（仍是同一份），且能解出原 key
            assert after.api_key == sealed_before
            assert credential_crypto.reveal(after.api_key, user_id=1, capability="llm") == (
                "sk-user-secret-key"
            )
        finally:
            db.close()

    def test_unknown_capability_rejected(self, api):
        client, H = api
        _set_mode("optional")
        H.as_user(1)
        assert client.put("/api/me/credentials/bogus", json=_payload()).status_code == 400

    def test_missing_fields_rejected(self, api):
        client, H = api
        _set_mode("optional")
        H.as_user(1)
        # 三者必填：缺任何一个都应 422（Pydantic 校验）
        for field in ("base_url", "api_key", "model_name"):
            body = _payload()
            body.pop(field)
            assert client.put("/api/me/credentials/llm", json=body).status_code == 422


# ---------------------------------------------------------------- SSRF


class TestBaseUrlSafety:
    def test_private_address_rejected(self, api, monkeypatch):
        """用户填内网地址必须被拦——否则普通用户获得内网探测原语。"""
        client, H = api
        monkeypatch.delenv("OPENNOTER_PROBE_ALLOW_PRIVATE", raising=False)
        _set_mode("optional")
        H.as_user(1)

        resp = client.put(
            "/api/me/credentials/llm",
            json=_payload(base_url="http://169.254.169.254/latest/meta-data"),
        )
        assert resp.status_code == 400
        assert "不安全" in resp.text

    def test_loopback_rejected(self, api, monkeypatch):
        client, H = api
        monkeypatch.delenv("OPENNOTER_PROBE_ALLOW_PRIVATE", raising=False)
        _set_mode("optional")
        H.as_user(1)

        resp = client.put(
            "/api/me/credentials/llm", json=_payload(base_url="http://127.0.0.1:11434/v1")
        )
        assert resp.status_code == 400

    def test_non_http_scheme_rejected(self, api, monkeypatch):
        client, H = api
        monkeypatch.delenv("OPENNOTER_PROBE_ALLOW_PRIVATE", raising=False)
        _set_mode("optional")
        H.as_user(1)

        resp = client.put(
            "/api/me/credentials/llm", json=_payload(base_url="file:///etc/passwd")
        )
        assert resp.status_code == 400
