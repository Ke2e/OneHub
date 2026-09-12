"""W2 任务 1 RED：管理面 /api/keys CRUD 测试先行。

范围（Asize 批准）：POST（创建 Key，明文 sk- 一次）+ GET（列表脱敏）+ DELETE（软删）。
- POST：201 返回明文 key（sk- 前缀）与 key_prefix；桩里 key_hash=SHA256(明文)；
  白名单 model_whitelist 原样落库（模型白名单语义，任务 3 消费）
- GET：列表不含 key_hash（密钥不可回读），含 prefix/name/status/白名单
- DELETE：204，状态置 revoked（软删——usage_records 外键引用，硬删破坏引用完整性）
- 管理面鉴权：无 JWT → 401（contracts：管理面 = JWT）
"""

import pytest
from fastapi.testclient import TestClient

import app.api.admin.keys as keys_module
from app.services.keys import hash_sk_key
from app.main import create_app
from app.models import ApiKey
from tests._fake_db import FakeSession


def _shared_factory():
    """返回持有单例 FakeSession 的 AsyncSession 工厂（跨请求共享状态）。"""

    def factory(*args, **kwargs):
        return factory.shared

    factory.shared = FakeSession()
    return factory


def _jwt_header():
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(sub='1', tenant_id=1)}"}


@pytest.fixture
def client(monkeypatch, request):
    """桩化 keys 模块 AsyncSession（共享单例），挂真实 app（create→list 同状态）。"""
    monkeypatch.setattr(keys_module, "AsyncSession", _shared_factory())
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def _create_key(client, **overrides):
    payload = {"name": "prod-key", "model_whitelist": ["deepseek-v4-flash-0731"]}
    payload.update(overrides)
    return client.post("/api/keys", headers=_jwt_header(), json=payload)


def test_create_key_returns_plaintext_once(client, monkeypatch):
    """POST /api/keys：201 + 明文 sk-；桩内 key_hash = SHA256(明文)；白名单落库。"""
    factory = _shared_factory()
    monkeypatch.setattr(keys_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        resp = _create_key(c)
        assert resp.status_code == 201

    body = resp.json()
    assert body["key"].startswith("sk-")
    assert body["key_prefix"] == body["key"][:10]
    assert body["name"] == "prod-key"
    assert body["model_whitelist"] == ["deepseek-v4-flash-0731"]

    stored: ApiKey = factory.shared.stored(ApiKey)[0]
    assert stored.key_hash == hash_sk_key(body["key"])  # 只存哈希
    assert stored.key_prefix == body["key_prefix"]
    assert stored.model_whitelist == ["deepseek-v4-flash-0731"]
    assert stored.status == "active"
    assert stored.tenant_id == 1


def test_create_key_requires_jwt(client, monkeypatch):
    """无 JWT → 401 authentication_error（管理面契约）。"""
    resp = client.post("/api/keys", json={"name": "x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_list_keys_without_hash(client):
    """GET /api/keys：列表含 prefix 但不含 key_hash（密钥不可回读）。"""
    created = _create_key(client)
    assert created.status_code == 201

    resp = client.get("/api/keys", headers=_jwt_header())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["key_prefix"] == created.json()["key_prefix"]
    assert item["name"] == "prod-key"
    assert item["status"] == "active"
    assert "key_hash" not in item
    assert "key" not in item  # 明文绝不回读


def test_delete_key_soft_revokes(client, monkeypatch):
    """DELETE /api/keys/{id}：204，status → revoked（软删保外键引用）。"""
    factory = _shared_factory()
    monkeypatch.setattr(keys_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        created = _create_key(c)
        key_id = created.json()["id"]
        resp = c.delete(f"/api/keys/{key_id}", headers=_jwt_header())
        assert resp.status_code == 204

    stored: ApiKey = factory.shared.stored(ApiKey)[0]
    assert stored.status == "revoked"


def test_delete_missing_key_raises(client, monkeypatch):
    """删除不存在的 key → 404 invalid_request_error。"""
    resp = client.delete("/api/keys/999", headers=_jwt_header())
    assert resp.status_code == 404