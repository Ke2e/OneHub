"""W3 任务 1 RED：网关端点余额预检集成——余额不足 402，放行进转发。

链路：鉴权（桩）→ model enabled（桩）→ 白名单 → 限流 → 幂等 → **余额预检** → 并发槽
→ 转发。本测试用 FakeBilling + FakeLimiter + FakeProvider 验证预检分支语义与调用
位置（预检在幂等回放后、并发槽前）。precheck 的缓存/查库逻辑在 test_billing.py 独立覆盖。
"""

import pytest
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
from app.main import create_app
from app.models import ApiKey, Model
from app.services.keys import hash_sk_key
from tests._fake_billing import FakeBilling
from tests._fake_db import FakeSession

VALID_KEY = "sk-test-gateway-key"
MODEL = "deepseek-v4-flash-0731"


class FakeLimiter:
    """记录调用顺序 + 可编程结果（check/acquire 均放行）。"""

    def __init__(self):
        self.calls: list[tuple] = []

    async def check(self, key_id, rpm_limit, tpm_limit, tokens, now=None):
        self.calls.append(("check", key_id))
        return (True, 0)

    def try_acquire(self) -> bool:
        self.calls.append(("acquire",))
        return True

    def release(self):
        self.calls.append(("release",))


class FakeProvider:
    async def chat(self, req):
        return {
            "id": "chatcmpl-billing",
            "object": "chat.completion",
            "created": 0,
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def aclose(self):
        pass


@pytest.fixture
def client(monkeypatch):
    """预置 ApiKey + enabled Model；patch 鉴权 AsyncSession；注入 FakeLimiter + FakeBilling。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [
            ApiKey(
                tenant_id=7,
                name="test-key",
                key_prefix=VALID_KEY[:10],
                key_hash=hash_sk_key(VALID_KEY),
                status="active",
                expires_at=None,
                model_whitelist=None,
                rpm_limit=60,
                tpm_limit=100000,
            ),
            Model(model_name=MODEL, enabled=True),
        ]
    )
    import app.core.security as security_module

    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        limiter = FakeLimiter()
        billing = FakeBilling()
        c.app.dependency_overrides[gateway_module.get_rate_limiter] = lambda: limiter
        c.app.dependency_overrides[gateway_module.get_billing] = lambda: billing
        c.app.state.fake_limiter = limiter
        c.app.state.fake_billing = billing
        c.app.state.deepseek_provider = FakeProvider()
        yield c


def _chat(client, stream: bool = False):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": stream},
    )


def test_insufficient_balance_returns_402(client):
    """余额预检不足 → 402 insufficient_quota / insufficient_balance，不占并发槽不转发。"""
    billing: FakeBilling = client.app.state.fake_billing
    billing.insufficient = True

    resp = _chat(client)

    assert resp.status_code == 402
    body = resp.json()["error"]
    assert body["type"] == "insufficient_quota"
    assert body["code"] == "insufficient_balance"
    assert body["message"] == "insufficient balance"
    # 预检发生在并发槽占用与转发之前：仅 check →(无 acquire、无 release)
    limiter: FakeLimiter = client.app.state.fake_limiter
    assert [c[0] for c in limiter.calls] == ["check"]


def test_sufficient_balance_forwards_then_releases(client):
    """余额充足 → 进并发槽转发，成功 release。预检在 check 后、acquire 前。"""
    billing: FakeBilling = client.app.state.fake_billing
    limiter: FakeLimiter = client.app.state.fake_limiter

    resp = _chat(client)

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ok"
    # 预检（无 limiter 行为）位于限流 check 与并发槽 acquire 之间
    assert [c[0] for c in limiter.calls] == ["check", "acquire", "release"]
    # 预检按 key 所属 tenant 定位
    assert billing.calls[0][0] == 7