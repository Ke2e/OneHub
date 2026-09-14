"""W4 任务 1 RED：SmartRouter 编排单测——熔断过滤 + 加权挑选 + 退避重试。

用 Fake 熔断器（可编程 can_try / record_failure / record_success）与可编程
upstream，验证编排层行为（不启真 Redis / 上游）：
- 只挑可用且未熔断的渠道（skip channels.status=blocked / breaker open）
- 全不可用 → UpstreamError(503)
- 加权轮询在可用集内分布
- 转发失败 → record_failure + 从候选剔除 → 重试下一个 → 全失败 503
- 转发成功 → record_success，返回结果
"""

import pytest

from app.core.errors import UpstreamError
from app.services.circuit_breaker import CircuitBreaker
from app.services.router import Candidate, WeightedRobin
from app.services.smart_router import SmartRouter


class FakeBreaker:
    """熔断器桩：可编程 can_try / record_*，记录调用。"""

    def __init__(self, open_channels: set[int] | None = None):
        self.open_channels = set(open_channels or [])
        self.failures: list[int] = []
        self.successes: list[int] = []
        self.closed = False

    async def can_try(self, channel_id, now=None):
        return channel_id not in self.open_channels

    async def record_failure(self, channel_id, now=None):
        self.failures.append(channel_id)
        return False

    async def record_success(self, channel_id):
        self.successes.append(channel_id)

    async def aclose(self):
        self.closed = True


class OkUpstream:
    """upstream callable：记录被调的渠道，全成功。"""

    def __init__(self):
        self.calls: list[int] = []

    async def __call__(self, channel_id):
        self.calls.append(channel_id)
        return f"ok-{channel_id}"


def _cands(*channels):
    return [Candidate(channel_id=cid, weight=w, available=a) for cid, w, a in channels]


@pytest.mark.asyncio
async def test_pick_channel_skips_blocked_and_open():
    """只挑可用且未熔断的渠道；blocked(status) 与 open(breaker) 都被跳过。"""
    breaker = FakeBreaker(open_channels={3})
    router = SmartRouter(breaker)
    cands = _cands((1, 10, False), (2, 10, True), (3, 10, True))  # 1 blocked, 3 open
    picked = await router.pick_channel(cands, now=100)
    assert picked.channel_id == 2


@pytest.mark.asyncio
async def test_pick_channel_all_unavailable_returns_none():
    """全部不可用 → None（无可用渠道）。"""
    router = SmartRouter(FakeBreaker(open_channels={1, 2}))
    cands = _cands((1, 10, True), (2, 10, True))
    assert (await router.pick_channel(cands, now=100)) is None


@pytest.mark.asyncio
async def test_forward_success_records_success_and_returns():
    """单一可用渠道转发成功 → 返回结果 + record_success。"""
    breaker = FakeBreaker()
    router = SmartRouter(breaker, max_attempts=3)
    up = OkUpstream()
    out = await router.forward(_cands((7, 10, True)), up, now=100)
    assert out == "ok-7"
    assert up.calls == [7]
    assert breaker.successes == [7]
    assert breaker.failures == []


@pytest.mark.asyncio
async def test_forward_retries_next_after_failure(monkeypatch):
    """渠道1失败 → record_failure + 剔除 → 重试渠道2成功。"""
    monkeypatch.setattr(SmartRouter, "_sleep", lambda self, d: __import__("asyncio").sleep(0))
    breaker = FakeBreaker()
    router = SmartRouter(breaker, max_attempts=3)

    class Flaky:
        def __init__(self):
            self.fail_first = True

        async def __call__(self, channel_id):
            if channel_id == 1 and self.fail_first:
                self.fail_first = False
                raise UpstreamError("boom", status_code=502)
            return f"ok-{channel_id}"

    up = Flaky()
    out = await router.forward(_cands((1, 10, True), (2, 10, True)), up, now=100)
    assert out == "ok-2"
    assert up.fail_first is False  # 渠道1只被调一次
    assert breaker.failures == [1]
    assert breaker.successes == [2]


@pytest.mark.asyncio
async def test_forward_all_candidates_fail_raises_503(monkeypatch):
    """所有候选都失败 → UpstreamError 503（不再可路由渠道）。"""
    monkeypatch.setattr(SmartRouter, "_sleep", lambda self, d: __import__("asyncio").sleep(0))
    breaker = FakeBreaker()
    router = SmartRouter(breaker, max_attempts=3)

    class AlwaysFail:
        async def __call__(self, channel_id):
            raise UpstreamError("boom", status_code=502)

    with pytest.raises(UpstreamError) as exc:
        await router.forward(_cands((1, 10, True), (2, 10, True)), AlwaysFail(), now=100)
    assert exc.value.status_code == 503
    assert set(breaker.failures) == {1, 2}


@pytest.mark.asyncio
async def test_forward_respects_max_attempts(monkeypatch):
    """单一候选持续失败：尝试不超出 max_attempts，且该渠道失败被记录。"""
    monkeypatch.setattr(SmartRouter, "_sleep", lambda self, d: __import__("asyncio").sleep(0))
    breaker = FakeBreaker()
    router = SmartRouter(breaker, max_attempts=2)

    class AlwaysFail:
        async def __call__(self, channel_id):
            raise UpstreamError("boom", status_code=502)

    with pytest.raises(UpstreamError):
        await router.forward(_cands((5, 10, True)), AlwaysFail(), now=100)
    assert len(breaker.failures) >= 1


@pytest.mark.asyncio
async def test_forward_respects_weighted_distribution(monkeypatch):
    """两个都可用渠道：加权轮询在两者间分布（weight 3:1 → 多数给 weight=2 的渠道）。"""
    monkeypatch.setattr(SmartRouter, "_sleep", lambda self, d: __import__("asyncio").sleep(0))
    breaker = FakeBreaker()
    router = SmartRouter(breaker, max_attempts=3)
    up = OkUpstream()
    cands = _cands((1, 1, True), (2, 3, True))
    for _ in range(4):
        await router.forward(cands, up, now=100)
    assert set(up.calls) == {1, 2}
    assert up.calls.count(2) > up.calls.count(1)