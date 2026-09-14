"""W4 任务 2：管理面 channels CRUD + 熔断实时态端点离线测试。

- FakeSession 桩（塞 Channel / UsageRecord）测 CRUD 全路径。
- 熔断实时态依赖 request.app.state.circuit_breaker.get_state：
  测试用 FakeBreaker 替换（只读展示，无真实 Redis/EVAL）。
- require_admin JWT 头鉴权。
"""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.admin.channels as ch_module
from app.main import create_app
from app.models import Channel, UsageRecord
from tests._fake_db import FakeSession

from decimal import Decimal


class _FakeBreaker:
    """熔断实时态桩：可编程的 per-channel 读取。"""

    def __init__(self, states=None):
        self._states = states or {}

    def set(self, cid, state):
        self._states[cid] = state

    async def get_state(self, cid):
        return self._states.get(
            cid, {"state": "closed", "failure_count": 0, "opened_at": 0.0}
        )

    async def aclose(self):
        """lifespan 退出会统一 aclose app.state 单例，桩 no-op。"""
        return None


def _jwt_header():
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(sub='1', tenant_id=1)}"}


def _shared_factory(initial=None):
    def factory(*args, **kwargs):
        return factory.shared
    factory.shared = FakeSession(initial=initial or [])
    return factory


@pytest.fixture
def client_and_breaker(monkeypatch):
    factory = _shared_factory()
    breaker = _FakeBreaker()
    monkeypatch.setattr(ch_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.app.state.circuit_breaker = breaker
        yield c, factory, breaker


def _channel(initial=None):
    if initial is None:
        initial = [
            Channel(name="deepseek-main", provider="deepseek", base_url="https://a/v1",
                    api_key_encrypted="env-required", status="healthy", weight=10),
            Channel(name="deepseek-backup", provider="deepseek", base_url="https://b/v1",
                    api_key_encrypted="env-required", status="healthy", weight=5),
        ]
    return initial


def test_list_channels_with_breaker_state(monkeypatch, client_and_breaker):
    """GET 列表：DB 字段 + 熔断实时态（Redis 三态注入）。"""
    client, factory, breaker = client_and_breaker
    [c1, c2] = _channel()
    c1.id, c2.id = 1, 2
    factory.shared = FakeSession(initial=[c1, c2])
    breaker.set(1, {"state": "open", "failure_count": 7, "opened_at": 1234.5})
    resp = client.get("/api/channels", headers=_jwt_header())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    main = next(i for i in items if i["name"] == "deepseek-main")
    assert main["weight"] == 10
    assert main["status"] == "healthy"
    assert main["breaker_state"] == {"state": "open", "failure_count": 7, "opened_at": 1234.5}
    backup = next(i for i in items if i["name"] == "deepseek-backup")
    assert backup["breaker_state"]["state"] == "closed"


def test_create_channel(client_and_breaker, monkeypatch):
    """POST 201：占位密钥 + 初态熔断；DB 落库。"""
    client, factory, breaker = client_and_breaker
    resp = client.post(
        "/api/channels",
        headers=_jwt_header(),
        json={"name": "zhipu-main", "base_url": "https://z/v1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "zhipu-main"
    stored: Channel = factory.shared.stored(Channel)[0]
    assert stored.base_url == "https://z/v1"
    assert stored.api_key_encrypted == "env-required"  # 未传 → 占位
    assert stored.weight == 10  # server_default 由桩默认? 显式构造为 10


def test_create_channel_duplicate_conflicts(monkeypatch, client_and_breaker):
    """重名 → 409 conflict_error（表无唯一约束，查重式幂等）。"""
    client, factory, breaker = client_and_breaker
    factory.shared = FakeSession(initial=_channel())
    resp = client.post(
        "/api/channels",
        headers=_jwt_header(),
        json={"name": "deepseek-main", "base_url": "https://a/v1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["type"] == "conflict_error"


def test_update_channel_patch(monkeypatch, client_and_breaker):
    """PATCH：局部更新 weight/status，其余保留。"""
    client, factory, breaker = client_and_breaker
    [c1, c2] = _channel()
    c1.id, c2.id = 1, 2
    factory.shared = FakeSession(initial=[c1, c2])
    resp = client.patch(
        "/api/channels/1",
        headers=_jwt_header(),
        json={"status": "manual_down", "weight": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "manual_down"
    assert body["weight"] == 3
    stored: Channel = factory.shared.stored(Channel)[0]
    assert stored.status == "manual_down"


def test_update_missing_channel_404(client_and_breaker):
    """PATCH 不存在渠道 → 404 invalid_request_error。"""
    client, factory, breaker = client_and_breaker
    resp = client.patch("/api/channels/999", headers=_jwt_header(), json={"status": "x"})
    assert resp.status_code == 404


def test_delete_channel_no_ref_204(monkeypatch, client_and_breaker):
    """DELETE 无 usage 引用 → 204 硬删。"""
    client, factory, breaker = client_and_breaker
    [c1, c2] = _channel()
    c1.id, c2.id = 1, 2
    factory.shared = FakeSession(initial=[c1, c2])
    resp = client.delete("/api/channels/1", headers=_jwt_header())
    assert resp.status_code == 204
    assert len(factory.shared.stored(Channel)) == 1


def test_delete_channel_referenced_conflicts(monkeypatch, client_and_breaker):
    """DELETE 被 usage_records 引用 → 409（保引用完整性）。"""
    client, factory, breaker = client_and_breaker
    [c1] = _channel()[:1]
    c1.id = 1
    usage = UsageRecord(
        request_id=uuid4(), model="m1", channel_id=1,
        prompt_tokens=1, completion_tokens=1, status_code=200,
        cost=Decimal("0.01"), created_at=datetime.now(timezone.utc),
    )
    factory.shared = FakeSession(initial=[c1, usage])
    resp = client.delete("/api/channels/1", headers=_jwt_header())
    assert resp.status_code == 409
    assert resp.json()["error"]["type"] == "conflict_error"


def test_delete_missing_channel_404(client_and_breaker):
    """DELETE 不存在渠道 → 404。"""
    client, factory, breaker = client_and_breaker
    resp = client.delete("/api/channels/999", headers=_jwt_header())
    assert resp.status_code == 404


def test_channels_requires_jwt(client_and_breaker):
    """无 JWT → 401 authentication_error。"""
    client, factory, breaker = client_and_breaker
    resp = client.get("/api/channels")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"