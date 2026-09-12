"""W2 任务 2 RED：网关面鉴权中间件全分支（api_keys 表哈希校验 → 状态/过期/白名单）。

迁移语义：require_gateway_api_key 从 .env 固定 key（GATEWAY_API_KEY 配置比对）迁到
api_keys 表——Bearer sk-key → SHA-256 哈希 → key_hash 查表 → status/expires_at 校验。
明文永不回读（返回实体仅哈希/前缀，见 test_admin_keys 脱敏约定）。

鉴权分支（端点级桩化，GET /v1/models 作观察面）：
- 有效 key（active 未过期）→ 200
- 未知明文（hash 无记录）→ 401
- status=revoked → 401
- expires_at 已过 → 401
- 无 Authorization → 401
- scheme 非 Bearer → 401

白名单（model_whitelist，端点消费）：
- check_model_whitelist 纯函数 4 分支（无白名单放行 / 包含放行 / 不包含拒绝）
- chat 端点集成：白名单不含请求 model → 404 model_not_found（model 存在但 key 无权）
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
import app.core.security as security_module
from app.main import create_app
from app.models import ApiKey, Model
from app.services.keys import check_model_whitelist, hash_sk_key
from tests._fake_db import FakeSession

VALID_KEY = "sk-test-gateway-key"

ENABLED = ["deepseek-v4-flash-0731", "senseaudio-s2"]


def _api_key(**overrides) -> ApiKey:
    base = {
        "tenant_id": 1,
        "name": "test-key",
        "key_prefix": VALID_KEY[:10],
        "key_hash": hash_sk_key(VALID_KEY),
        "status": "active",
        "expires_at": None,
        "model_whitelist": None,
    }
    base.update(overrides)
    return ApiKey(**base)


@pytest.fixture
def client(monkeypatch):
    """预置 ApiKey + enabled Model，patch 鉴权与端点两处 AsyncSession 指向同一桩。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [_api_key(), *[Model(model_name=m, enabled=True) for m in ENABLED]]
    )
    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def _get(client, auth: str | None = None):
    headers = {"Authorization": auth} if auth else {}
    return client.get("/v1/models", headers=headers)


# ── 鉴权分支 ──

def test_valid_active_key_passes(client):
    """分支 1 有效：active + 未过期 → 200。"""
    assert _get(client, f"Bearer {VALID_KEY}").status_code == 200


def test_unknown_key_raises_401(client):
    """分支 2 未知明文：hash 无记录 → 401。"""
    resp = _get(client, "Bearer sk-unknown-key")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_revoked_key_raises_401(client, monkeypatch):
    """分支 3 状态：status=revoked → 401（软删后不可用）。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession([_api_key(status="revoked")])
    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        assert _get(c, f"Bearer {VALID_KEY}").status_code == 401


def test_expired_key_raises_401(client, monkeypatch):
    """分支 4 过期：expires_at 早于当前 → 401。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [_api_key(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))]
    )
    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        assert _get(c, f"Bearer {VALID_KEY}").status_code == 401


def test_missing_header_raises_401(client):
    """分支 5 缺失：无 Authorization → 401。"""
    assert _get(client).status_code == 401


def test_non_bearer_scheme_raises_401(client):
    """分支 6 格式：scheme 非 Bearer → 401。"""
    assert _get(client, f"Basic {VALID_KEY}").status_code == 401


# ── 白名单 ──

def test_whitelist_empty_allows(client):
    """纯函数：model_whitelist 为空/None → 全部放行。"""
    key = _api_key()
    assert check_model_whitelist(key, "any-model") is True


def test_whitelist_contains_model_allows(client):
    """纯函数：白名单包含请求 model → 放行。"""
    key = _api_key(model_whitelist=["deepseek-v4-flash-0731"])
    assert check_model_whitelist(key, "deepseek-v4-flash-0731") is True


def test_whitelist_missing_model_denies(client):
    """纯函数：白名单非空且不含请求 model → 拒绝。"""
    key = _api_key(model_whitelist=["deepseek-v4-flash-0731"])
    assert check_model_whitelist(key, "senseaudio-s2") is False


def test_whitelist_denied_model_returns_404(client, monkeypatch):
    """端点集成：模型全局 enabled 但 key 白名单不含 → 404 model_not_found。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [
            _api_key(model_whitelist=["deepseek-v4-flash-0731"]),
            *[Model(model_name=m, enabled=True) for m in ENABLED],
        ]
    )
    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        resp = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {VALID_KEY}"},
            json={
                "model": "senseaudio-s2",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"