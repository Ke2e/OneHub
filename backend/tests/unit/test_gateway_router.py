"""W4 任务 1 集成 RED：网关端点多渠道智能路由——封主自动切备 + 用量渠道归属。

链路：鉴权（桩）→ 同 model_name 多行 enabled（channel 1/2）→ 候选收集
（权重/可用性取自 channels.status）→ smart_router.forward（桩，模拟熔断剔除+
换渠道）→ _provider_for_channel 按渠道实例化 provider → 成功返回。

本测试验证端点把候选/渠道正确接线给路由层，并断言转发实际走的渠道与用量事件
channel_id 一致。熔断三态/退避本身在 test_circuit_breaker.py / test_smart_router.py
独立覆盖，此处专注集成语义。
"""

import pytest
from contextlib import contextmanager
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
from app.core.errors import UpstreamError
from app.main import create_app
from app.models import ApiKey, Channel, Model
from app.schemas.chat import ChatCompletionResponse, Choice, Message, Usage
from app.services.keys import hash_sk_key
from app.services.smart_router import SmartRouter
from tests._fake_billing import FakeBilling
from tests._fake_db import FakeSession
from tests._fake_usage import FakeUsageProducer

VALID_KEY = "sk-test-gateway-key"
MODEL = "deepseek-v4-flash-0731"
CH1, CH2 = 1, 2


class FakeLimiter:
    """限流桩：check/acquire/release 均放行，仅记录顺序。"""

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


class FakeSmartRouter:
    """路由桩：语义等价 forward/pick_channel（逐候选跳过不可用→尝试→失败换下一个）。"""

    async def forward(self, candidates, upstream, now=None):
        for c in candidates:
            if not c.available:
                continue
            try:
                return await upstream(c.channel_id)
            except Exception:
                continue  # 渠道失败 → 换下一个候选
        raise UpstreamError(message="all channels unavailable", status_code=503)

    async def pick_channel(self, candidates, now=None):
        for c in candidates:
            if c.available:
                return c
        return None


class FakeProvider:
    """渠道 provider 桩：按失败白名单决定抛错或成功，记录调用次数。"""

    def __init__(self, channel_id: int, fail: set[int]):
        self.channel_id = channel_id
        self.fail = fail
        self.calls = 0

    async def chat(self, req):
        self.calls += 1
        if self.channel_id in self.fail:
            raise UpstreamError(message="upstream server error", status_code=502)
        return ChatCompletionResponse(
            id=f"chatcmpl-{self.channel_id}",
            created=0,
            model=req.model,
            choices=[Choice(index=0, message=Message(role="assistant", content=f"ok-{self.channel_id}"), finish_reason="stop")],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def _make_session(statuses: dict[int, str]) -> FakeSession:
    """预置 ApiKey + 两条同 model_name 的 Channel/Model（status 可按渠道定制）。"""
    return FakeSession(
        [
            ApiKey(
                id=1,
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
            Channel(id=CH1, name="main", provider="deepseek", base_url="https://a/v1",
                    api_key_encrypted="k", weight=10, status=statuses[CH1]),
            Channel(id=CH2, name="backup", provider="deepseek", base_url="https://b/v1",
                    api_key_encrypted="k", weight=10, status=statuses[CH2]),
            Model(model_name=MODEL, channel_id=CH1, enabled=True),
            Model(model_name=MODEL, channel_id=CH2, enabled=True),
        ]
    )


@pytest.fixture
def client(monkeypatch):
    """预置多渠道数据 + 全放行桩；override 智能路由；劫持 provider 工厂成桩。"""

    @contextmanager
    def _build(statuses: dict[int, str]):
        factory = lambda *a, **k: factory.shared
        factory.shared = _make_session(statuses)
        import app.core.security as security_module

        monkeypatch.setattr(security_module, "AsyncSession", factory)
        monkeypatch.setattr(gateway_module, "AsyncSession", factory)

        providers: dict[int, FakeProvider] = {}
        fail: set[int] = set()

        def _factory(request, channel):
            if channel.id not in providers:
                providers[channel.id] = FakeProvider(channel.id, fail)
            return providers[channel.id]

        monkeypatch.setattr(gateway_module, "_provider_for_channel", _factory)

        with TestClient(create_app(), raise_server_exceptions=False) as c:
            usage = FakeUsageProducer()
            c.app.dependency_overrides[gateway_module.get_rate_limiter] = lambda: FakeLimiter()
            c.app.dependency_overrides[gateway_module.get_billing] = lambda: FakeBilling()
            c.app.dependency_overrides[gateway_module.get_usage_producer] = lambda: usage
            c.app.dependency_overrides[gateway_module.get_smart_router] = lambda: FakeSmartRouter()
            c.app.state.fail = fail
            c.app.state.providers = providers
            c.app.state.fake_usage = usage
            yield c

    return _build


class FakeBreaker:
    """熔断器桩（真实 SmartRouter 用）：open_channels 内视为 OPEN → can_try 拒绝。

    record_failure 恒返回 True（模拟失败即开断），使实时路由跳过退避休眠——
    专注于验证「熔断过滤 → 加权挑选 → 切备」的端点接线，而非退避时长本身。
    """

    def __init__(self, open_channels: set[int] | None = None):
        self.open_channels = set(open_channels or [])
        self.failures: list[int] = []
        self.successes: list[int] = []

    async def can_try(self, channel_id, now=None):
        return channel_id not in self.open_channels

    async def record_failure(self, channel_id, now=None):
        self.failures.append(channel_id)
        return True  # OPEN（跳过退避，立即换候选）

    async def record_success(self, channel_id):
        self.successes.append(channel_id)

    async def aclose(self):
        pass


@pytest.fixture
def real_router_client(monkeypatch):
    """与 client 同构，但注入【真实 SmartRouter + FakeBreaker】——端到端冒烟验收。"""

    @contextmanager
    def _build(statuses: dict[int, str], open_channels: set[int]):
        factory = lambda *a, **k: factory.shared
        factory.shared = _make_session(statuses)
        import app.core.security as security_module

        monkeypatch.setattr(security_module, "AsyncSession", factory)
        monkeypatch.setattr(gateway_module, "AsyncSession", factory)

        providers: dict[int, FakeProvider] = {}

        def _factory(request, channel):
            if channel.id not in providers:
                providers[channel.id] = FakeProvider(channel.id, fail=set())
            return providers[channel.id]

        monkeypatch.setattr(gateway_module, "_provider_for_channel", _factory)

        with TestClient(create_app(), raise_server_exceptions=False) as c:
            usage = FakeUsageProducer()
            c.app.dependency_overrides[gateway_module.get_rate_limiter] = lambda: FakeLimiter()
            c.app.dependency_overrides[gateway_module.get_billing] = lambda: FakeBilling()
            c.app.dependency_overrides[gateway_module.get_usage_producer] = lambda: usage
            # 关键：不 override get_smart_router → 端点用 app.state 真实 SmartRouter
            breaker = FakeBreaker(open_channels=open_channels)
            c.app.state.smart_router = SmartRouter(breaker)
            c.app.state.breaker = breaker
            c.app.state.providers = providers
            c.app.state.fake_usage = usage
            yield c

    return _build


def _chat(client, stream: bool = False):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": stream},
    )


def test_main_fails_auto_switches_to_backup(client):
    """主渠道转发失败 → 自动切到备份渠道成功，用量绑定实际渠道（CH2）。"""
    with client({CH1: "healthy", CH2: "healthy"}) as c:
        c.app.state.fail.add(CH1)  # 主渠道抛 502

        resp = _chat(c)

        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "ok-2"
        # 主渠道被尝试过、备份成功：切换发生
        assert c.app.state.providers[CH1].calls == 1
        assert c.app.state.providers[CH2].calls == 1
        # 用量事件归属实际生效渠道
        usage = c.app.state.fake_usage.emitted
        assert len(usage) == 1 and usage[0]["channel_id"] == CH2


def test_main_blocked_status_skipped_direct_to_backup(client):
    """主渠道 status != healthy（管理员封禁）→ 候选不可用直接跳过，备份成功。"""
    with client({CH1: "disabled", CH2: "healthy"}) as c:
        resp = _chat(c)

        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "ok-2"
        # 主渠道从未被实例化/调用
        assert CH1 not in c.app.state.providers
        assert c.app.state.providers[CH2].calls == 1


def test_main_healthy_restored_picked_first(client):
    """主渠道恢复 healthy → 再次成为首选候选（前向遍历先命中），切回主渠道。"""
    with client({CH1: "healthy", CH2: "healthy"}) as c:
        _chat(c)  # 首请求预热，两渠道均已可选

        # 重置：主渠道恢复 healthy，不设失败
        c.app.state.fail.clear()
        resp = _chat(c)

        assert resp.status_code == 200
        assert c.app.state.providers[CH1].calls == 2  # 主渠道再次被选中
        usage = c.app.state.fake_usage.emitted[-1]
        assert usage["channel_id"] in (CH1, CH2)


def test_all_channels_blocked_returns_503(client):
    """全部候选不可用（无 healthy）→ 非流式 forward 抛 503。"""
    with client({CH1: "disabled", CH2: "disabled"}) as c:
        resp = _chat(c)
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "api_error"


# ── 端到端冒烟：真实 SmartRouter + FakeBreaker（验收「封主切备、恢复切回」） ──

def test_smoke_main_open_auto_switches_to_backup(real_router_client):
    """主渠道熔断 OPEN（can_try=False）→ 真实路由跳过主、切到备份渠道成功。"""
    with real_router_client({CH1: "healthy", CH2: "healthy"}, open_channels={CH1}) as c:
        resp = _chat(c)

        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "ok-2"
        # 主渠道被熔断剔除，从未实例化；备份渠道承载成功
        assert CH1 not in c.app.state.providers
        assert c.app.state.providers[CH2].calls == 1
        # 真实路由在成功渠道记 success
        assert c.app.state.breaker.successes == [CH2]
        # 用量绑定实际渠道
        assert c.app.state.fake_usage.emitted[0]["channel_id"] == CH2


def test_smoke_main_recovers_switches_back(real_router_client):
    """主渠道冷却恢复（熔断关闭）→ 真实路由把流量切回主渠道。"""
    with real_router_client({CH1: "healthy", CH2: "healthy"}, open_channels={CH1}) as c:
        resp = _chat(c)  # 熔断期：走备份
        assert resp.json()["choices"][0]["message"]["content"] == "ok-2"
        assert CH1 not in c.app.state.providers

        # 主渠道恢复（模拟 cooldown 过后熔断关闭）
        c.app.state.breaker.open_channels.clear()
        resp = _chat(c)

        assert resp.status_code == 200
        assert c.app.state.providers[CH1].calls == 1  # 流量切回主渠道
        assert c.app.state.fake_usage.emitted[-1]["channel_id"] == CH1