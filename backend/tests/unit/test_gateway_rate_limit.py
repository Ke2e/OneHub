"""W2 任务 3 RED：网关端点限流集成——超限 429 + Retry-After 头，Semaphore 占满同 429。

链路：鉴权（桩）→ model enabled（桩）→ 白名单（放行）→ RateLimiter.check → Semaphore
→ 转发。本测试把 get_rate_limiter 依赖换成 fake limiter，验证限流分支的响应语义，
不触发真实转发（转发 200 路径由 T013/T014 覆盖）。
"""

import pytest
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
from app.main import create_app
from app.models import ApiKey, Model
from app.services.keys import hash_sk_key
from app.schemas.chat import ChatCompletionResponse, Choice, Message, Usage
from tests._fake_billing import FakeBilling
from tests._fake_db import FakeSession
from tests._fake_usage import FakeUsageProducer

VALID_KEY = "sk-test-gateway-key"
MODEL = "deepseek-v4-flash-0731"


class FakeLimiter:
    """记录调用顺序 + 可编程结果（check 全放行 / 拒绝；try_acquire 可用 / 占满）。"""

    def __init__(self, allowed: bool = True, acquire: bool = True):
        self.allowed = allowed
        self.acquire = acquire
        self.calls: list[tuple] = []

    async def check(self, key_id, rpm_limit, tpm_limit, tokens, now=None):
        self.calls.append(("check", key_id, rpm_limit, tpm_limit, tokens))
        if not self.allowed:
            return (False, 5)
        return (True, 0)

    def try_acquire(self) -> bool:
        self.calls.append(("acquire",))
        return self.acquire

    def release(self):
        self.calls.append(("release",))


@pytest.fixture
def client(monkeypatch):
    """预置 ApiKey + enabled Model，patch 鉴权 AsyncSession，并用 dependency_overrides
    替换限流依赖（FastAPI 依赖在路由注册时已捕获原函数引用，monkeypatch 模块属性无效）。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [
            ApiKey(
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
        c.app.dependency_overrides[gateway_module.get_rate_limiter] = lambda: limiter
        c.app.dependency_overrides[gateway_module.get_billing] = lambda: FakeBilling()
        c.app.dependency_overrides[gateway_module.get_usage_producer] = lambda: FakeUsageProducer()
        c.app.state.fake_limiter = limiter
        yield c


def _chat(client, stream: bool = False):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": stream},
    )


def test_rate_limited_returns_429_with_retry_after(client):
    """RPM/TPM 桶超限 → 429 rate_limit_error + Retry-After 头（廉价分支，不碰转发）。"""
    limiter: FakeLimiter = client.app.state.fake_limiter
    limiter.allowed = False

    resp = _chat(client)

    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"
    assert resp.headers.get("retry-after") == "5"
    # 拒绝发生在限流检查，阻塞在 try_acquire/转发之前
    assert [c[0] for c in limiter.calls] == ["check"]


def test_rate_limited_on_streaming(client):
    """流式请求同样在转发前限流 → 429（不产出 SSE）。"""
    client.app.state.fake_limiter.allowed = False
    resp = _chat(client, stream=True)
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "5"


def test_semaphore_full_returns_429(client):
    """桶放行但并发槽占满 → 429 + Retry-After=1。"""
    limiter: FakeLimiter = client.app.state.fake_limiter
    limiter.acquire = False

    resp = _chat(client)

    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"
    assert resp.headers.get("retry-after") == "1"
    assert [c[0] for c in limiter.calls] == ["check", "acquire"]


def test_allowed_acquires_and_releases(client):
    """桶放行 + 有并发槽 → 进入转发，success 后 release（finally 保证，无泄漏）。"""
    class FakeProvider:
        async def chat(self, req):
            return ChatCompletionResponse(
                id="chatcmpl-fake",
                created=0,
                model=req.model,
                choices=[Choice(index=0, message=Message(role="assistant", content="ok"), finish_reason="stop")],
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        async def aclose(self):
            pass  # lifespan 退出时调用

    client.app.state.deepseek_provider = FakeProvider()  # lifespan 已跑，覆盖单例防真连网
    limiter: FakeLimiter = client.app.state.fake_limiter

    resp = _chat(client)

    assert resp.status_code == 200
    ops = [c[0] for c in limiter.calls]
    assert ops == ["check", "acquire", "release"]  # 全链路 + 槽位配对