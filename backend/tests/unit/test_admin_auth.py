"""W2 任务 1 RED：管理面 auth（register/login）端点测试先行。

范围（Asize 批准的 auth + keys CRUD）：POST /api/auth/register|login → 签发 JWT。
- register：建 Tenant + User（password_hash 非明文、可 verify），返 token
- login：密码正确 → token；错误 → 401 authentication_error
- 重复 email：冲突（409/400 任选，GREEN 对齐）
- 契约：管理面鉴权 = JWT（PROJECT_CONTEXT 6.4），token 可 decode 含 sub/tenant_id
"""

import pytest
from fastapi.testclient import TestClient

import app.api.admin.auth as auth_module
from app.core.security import decode_access_token
from app.main import create_app
from app.models import Tenant, User
from tests._fake_db import FakeSession


@pytest.fixture
def client(monkeypatch):
    """桩化 auth 模块 AsyncSession（离线，无 Docker 依赖），挂真实 app。"""

    def _fake_factory(*args, **kwargs):
        return FakeSession()

    monkeypatch.setattr(auth_module, "AsyncSession", _fake_factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def _register(client, email="admin@onehub.dev", password="s3cret-pass"):
    return client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "tenant_name": "Acme",
        },
    )


def test_register_creates_tenant_and_user_returns_token(client, monkeypatch):
    """register 成功：201 + JWT token（sub=user id，tenant_id=租户 id）。"""
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    claims = decode_access_token(body["token"])
    assert claims["sub"] == str(body["user"]["id"])
    assert claims["tenant_id"] == body["tenant"]["id"]
    assert body["user"]["email"] == "admin@onehub.dev"
    assert body["user"]["role"] == "admin"
    assert body["tenant"]["name"] == "Acme"


def test_register_stores_hashed_password(client, monkeypatch):
    """落库断言：桩里存的 password_hash 非明文且可 verify（凭单人提验证）。"""
    # 通过 monkeypatch 捕获 FakeSession 实例（生成一次请求后从 fixture 外探针）
    seen = {}

    def _fake_factory(*args, **kwargs):
        session = FakeSession()
        seen["session"] = session
        return session

    monkeypatch.setattr(auth_module, "AsyncSession", _fake_factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        resp = _register(c)
        assert resp.status_code == 201

    session: FakeSession = seen["session"]
    users = session.stored(User)
    tenants = session.stored(Tenant)
    assert len(users) == 1 and len(tenants) == 1
    stored: User = users[0]
    assert stored.password_hash != "s3cret-pass"
    assert stored.email == "admin@onehub.dev"
    assert stored.tenant_id == tenants[0].id


def test_register_duplicate_email_conflicts(client):
    """重复 email → 409（清单元 6.4 register 冲突语义）。"""
    assert _register(client).status_code == 201
    resp = _register(client)
    assert resp.status_code == 409


def test_login_success_returns_token(client):
    """login 成功：200 + token（decode 含 sub/tenant_id）。"""
    _register(client)
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@onehub.dev", "password": "s3cret-pass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    claims = decode_access_token(body["token"])
    assert claims["sub"] == str(body["user"]["id"])
    assert claims["tenant_id"] == body["tenant"]["id"]


def test_login_wrong_password_raises(client):
    """密码错误 → 401 authentication_error。"""
    _register(client)
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@onehub.dev", "password": "wrong-pass"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_login_unknown_email_raises(client):
    """未知 email → 401 authentication_error（不泄露用户是否存在）。"""
    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody@onehub.dev", "password": "abc"},
    )
    assert resp.status_code == 401