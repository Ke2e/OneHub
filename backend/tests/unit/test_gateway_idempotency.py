"""W2 任务 4 RED：网关端点幂等集成——命中缓存回放不转发、占位者继续、409 conflict_error。

链路：鉴权（桩）→ model enabled（桩）→ 白名单（放行）→ limiter.check → 幂等
（get_or_acquire，只在非流式生效）→ try_acquire → 转发 → complete/cancel。
本测试用 dependency_overrides 换 get_idempotency 依赖为 FakeIdempotency（FakeLimiter
同款），验证端点侧分支语义；轮询/接管细节在服务层 test_idempotency.py。
"""

import pytest
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
from app.main import create_app
from app.models import ApiKey, Model
from app.services.idempotency import IdempotencyOutcome
from app.services.keys import hash_sk_key
from tests._fake_billing import FakeBilling
from tests._fake_db import FakeSession

VALID_KEY = "sk-test-gateway-key"
MODEL = "deepseek-v4-flash-0731"
IDEM_KEY = "11111111-2222-3333-4444-555555555555"


class FakeLimiter:
    """与 test_gateway_rate_limit 同款：记录调用顺序，可编程 check/acquire。"""

    def __init__(self, allowed: bool = True, acquire: bool = True):
        self.allowed = allowed
        self.acquire = acquire
        self.calls: list[tuple] = []

    async def check(self, key_id, rpm_limit, tpm_limit, tokens, now=None):
        self.calls.append(("check", key_id))
        return (True, 0) if self.allowed else (False, 5)

    def try_acquire(self) -> bool:
        self.calls.append(("acquire",))
        return self.acquire

    def release(self):
        self.calls.append(("release",))


class FakeIdempotency:
    """记录调用顺序 + 可编程 outcome（cached / acquired）。"""

    def __init__(self, outcome=IdempotencyOutcome.ACQUIRED, payload=None):
        self.outcome = outcome
        self.payload = payload
        self.calls: list[tuple] = []

    async def get_or_acquire(self, key_id, idem_key):
        self.calls.append(("get_or_acquire", key_id, idem_key))
        return self.outcome, self.payload

    async def complete(self, key_id, idem_key, payload):
        self.calls.append(("complete", key_id, idem_key))

    async def cancel(self, key_id, idem_key):
        self.calls.append(("cancel", key_id, idem_key))


CACHED_PAYLOAD = {
    "id": "chatcmpl-cached",
    "object": "chat.completion",
    "created": 0,
    "model": MODEL,
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "cached"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


class FakeProvider:
    async def chat(self, req):
        return {
            "id": "chatcmpl-fresh",
            "object": "chat.completion",
            "created": 0,
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fresh"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def aclose(self):
        pass


@pytest.fixture
def client(monkeypatch):
    """预置 ApiKey + enabled Model；patch 鉴权 AsyncSession；dependency_overrides
    换限流 + 幂等两个依赖（FastAPI 路由注册期已捕获原函数引用，monkeypatch 无效）。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [
            ApiKey(
                id=1,  # 显式主键：鉴权走 scalar 不 commit，自增分配不会发生
                tenant_id=1,
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
        idem = FakeIdempotency()
        c.app.dependency_overrides[gateway_module.get_rate_limiter] = lambda: limiter
        c.app.dependency_overrides[gateway_module.get_idempotency] = lambda: idem
        c.app.dependency_overrides[gateway_module.get_billing] = lambda: FakeBilling()
        c.app.state.fake_limiter = limiter
        c.app.state.fake_idem = idem
        c.app.state.deepseek_provider = FakeProvider()  # 防 lifespan 真连网
        yield c


def _chat(client, stream: bool = False, idem_key: str | None = IDEM_KEY):
    headers = {"Authorization": f"Bearer {VALID_KEY}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": stream},
    )


def test_no_idem_key_skips_idempotency(client):
    """不带 Idempotency-Key 头 → 幂等完全不介入（不调用 get_or_acquire），正常转发。"""
    resp = _chat(client, idem_key=None)
    assert resp.status_code == 200
    idem: FakeIdempotency = client.app.state.fake_idem
    assert idem.calls == []


def test_stream_with_key_skips_idempotency(client):
    """流式请求带 key → 幂等被跳过（SSE 不缓存），直接转发不受影响。"""
    resp = _chat(client, stream=True)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    idem: FakeIdempotency = client.app.state.fake_idem
    assert idem.calls == []


def test_cached_replays_without_forwarding(client):
    """命中缓存 → 直接回放缓存响应：不转发上游（provider 未调用）、不占并发槽。"""
    idem: FakeIdempotency = client.app.state.fake_idem
    idem.outcome = IdempotencyOutcome.CACHED
    idem.payload = CACHED_PAYLOAD

    resp = _chat(client)

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "cached"
    limiter: FakeLimiter = client.app.state.fake_limiter
    # 回放路径：限流 check 后即返回，无 acquire/转发/release/完成
    assert [c[0] for c in limiter.calls] == ["check"]
    assert idem.calls == [("get_or_acquire", 1, IDEM_KEY)]


def test_acquired_forwards_then_completes(client):
    """占位成功（ACQUIRED）→ 走限流槽 + 转发，成功后 complete 写缓存。"""
    idem: FakeIdempotency = client.app.state.fake_idem
    idem.outcome = IdempotencyOutcome.ACQUIRED

    resp = _chat(client)

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "fresh"
    limiter: FakeLimiter = client.app.state.fake_limiter
    ops = [c[0] for c in limiter.calls]
    assert ops == ["check", "acquire", "release"]  # 槽位配对
    assert idem.calls[0][0] == "get_or_acquire"
    assert idem.calls[1] == ("complete", 1, IDEM_KEY)  # 转发成功 → complete
    assert "cancel" not in [c[0] for c in idem.calls]


def test_acquired_but_semaphore_full_cancels_claim(client):
    """占位成功但并发槽被占满 → 429，且撤销占位（cancel）让后续请求可重试。"""
    idem: FakeIdempotency = client.app.state.fake_idem
    idem.outcome = IdempotencyOutcome.ACQUIRED
    limiter: FakeLimiter = client.app.state.fake_limiter
    limiter.acquire = False

    resp = _chat(client)

    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"
    ops = [c[0] for c in limiter.calls]
    assert ops == ["check", "acquire"]  # 槽满，转发未开始、槽未占用
    assert ("cancel", 1, IDEM_KEY) in idem.calls
    assert "complete" not in [c[0] for c in idem.calls]