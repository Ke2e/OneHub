"""W4 任务 1 RED：三态熔断器（保护清单，Redis Lua 手写，dev-tdd）。

规格（PROJECT_CONTEXT 6.2）：
    failure_count >= N(默认5) → OPEN（记 opened_at）
    now - opened_at >= cooldown(30s) → HALF_OPEN（放行 1 个探测请求）
    探测成功 → CLOSED（failure_count 清零）；失败 → 重新 OPEN

三态 CLOSED / OPEN / HALF_OPEN，状态存 Redis 供多 worker 共享。

策略（对齐令牌桶 _refill 对拍风格）：
- 状态迁移核心抽成纯函数 `_decide_allow` / `_after_failure` / `_after_success`，
  单测对拍数学逻辑。
- `CircuitBreaker` 封装手写 Lua EVAL（脚本本体在真 Redis 冒烟验证）。
  单测注入 fake eval 收取 ARGV / 回放返回值，覆盖 Python 侧 ARGV 组装与状态迁移打点。
"""

import pytest

from app.services.circuit_breaker import (
    CircuitBreaker,
    COOLDOWN,
    THRESHOLD,
    _after_failure,
    _after_success,
    _decide_allow,
)


# ── 1) 放行判定纯函数（Lua 对拍锚点） ──

def test_decide_allow_closed_always_allows():
    """CLOSED → 放行，状态保持 CLOSED。"""
    allow, state = _decide_allow("closed", opened_at=0, now=100)
    assert (allow, state) == (True, "closed")


def test_decide_allow_open_within_cooldown_rejects():
    """OPEN 且未过 cooldown → 拒绝，保持 OPEN（熔断生效期短路）。"""
    allow, state = _decide_allow("open", opened_at=100, now=120)
    assert (allow, state) == (False, "open")


def test_decide_allow_open_past_cooldown_enters_half_open():
    """OPEN 且已过 cooldown → 放行（进入 HALF_OPEN 探测）。"""
    allow, state = _decide_allow("open", opened_at=100, now=100 + COOLDOWN)
    assert (allow, state) == (True, "half_open")


def test_decide_allow_half_open_allows():
    """HALF_OPEN → 放行探测请求，状态保持 HALF_OPEN。"""
    allow, state = _decide_allow("half_open", opened_at=100, now=500)
    assert (allow, state) == (True, "half_open")


def test_decide_allow_boundary_exact_cooldown():
    """now - opened_at == cooldown 恰好满足 → 视为已过（>= 语义）。"""
    allow, _ = _decide_allow("open", opened_at=200, now=200 + COOLDOWN)
    assert allow is True


# ── 2) 失败后状态迁移纯函数 ──

def test_after_failure_closed_counts_up():
    """CLOSED 下连续失败：failure_count 递增，未达阈值前保持 CLOSED。"""
    state, fc = _after_failure("closed", failure_count=3)
    assert (state, fc) == ("closed", 4)


def test_after_failure_closed_reaches_threshold_opens():
    """CLOSED 失败计数达阈值（默认 5）→ OPEN。"""
    state, fc = _after_failure("closed", failure_count=THRESHOLD - 1)
    assert (state, fc) == ("open", THRESHOLD)


def test_after_failure_open_stays_open():
    """OPEN 下失败 → 保持 OPEN，不计入对象内计数（opened_at 已冷却锁定）。"""
    state, fc = _after_failure("open", failure_count=5)
    assert (state, fc) == ("open", 5)


def test_after_failure_half_open_reopens():
    """HALF_OPEN 探测失败 → 重新 OPEN（恢复尝试失败，继续熔断冷却）。"""
    state, fc = _after_failure("half_open", failure_count=5)
    assert (state, fc) == ("open", 5)


# ── 3) 成功后状态迁移纯函数 ──

def test_after_success_closed_resets():
    """CLOSED 下成功 → CLOSED，计数清零。"""
    state, fc = _after_success("closed", failure_count=3)
    assert (state, fc) == ("closed", 0)


def test_after_success_half_open_closes():
    """HALF_OPEN 探测成功 → CLOSED，计数清零（恢复切回）。"""
    state, fc = _after_success("half_open", failure_count=5)
    assert (state, fc) == ("closed", 0)


# ── 4) CircuitBreaker：Lua EVAL 封装 ──


class FakeRedis:
    """收集 eval 调用 + 可编程返回值的桩（对齐 _fake_db / rate_limit 离线风格）。"""

    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.closed = False

    async def eval(self, script, numkeys, *args):
        self.calls.append((script, numkeys, args))
        return self.result

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_breaker_record_failure_args():
    """record_failure：KEYS[1]=breaker:{cid}，ARGV=[now, threshold]。"""
    redis = FakeRedis(result=0)
    breaker = CircuitBreaker(redis)
    await breaker.record_failure(channel_id=7, now=1000)
    _, numkeys, args = redis.calls[-1]
    assert numkeys == 1
    assert args[0] == "breaker:7"
    assert args[1] == 1000.0
    assert args[2] == THRESHOLD


@pytest.mark.asyncio
async def test_breaker_record_failure_increments():
    """record_failure Lua 返回 1（本次失败计数到达阈值，已 OPEN）→ True。"""
    redis = FakeRedis(result=1)
    breaker = CircuitBreaker(redis)
    opened = await breaker.record_failure(channel_id=1, now=100)
    assert opened is True
    assert redis.calls[-1][2][1] == 100.0


@pytest.mark.asyncio
async def test_breaker_record_success_channel():
    """record_success：KEYS[1]=breaker:{cid}，成功后清计数。"""
    redis = FakeRedis(result="OK")
    breaker = CircuitBreaker(redis)
    await breaker.record_success(channel_id=3)
    _, numkeys, args = redis.calls[-1]
    assert numkeys == 1
    assert args[0] == "breaker:3"


@pytest.mark.asyncio
async def test_breaker_decide_allow_wraps_pure():
    """decide_allow 走 Lua：OPEN 未过 cooldown → 包装返回可路由=False。"""
    redis = FakeRedis(result=[0, "open"])
    breaker = CircuitBreaker(redis)
    ok = await breaker.can_try(channel_id=2, now=150)
    assert ok is False


@pytest.mark.asyncio
async def test_breaker_can_try_allows_when_closed():
    """CLOSED → Lua 放行 → 可路由=True。"""
    redis = FakeRedis(result=[1, "closed"])
    breaker = CircuitBreaker(redis)
    ok = await breaker.can_try(channel_id=2, now=150)
    assert ok is True


@pytest.mark.asyncio
async def test_aclose_closes_redis():
    """aclose → 底层 redis 连接关闭（lifespan 退出）。"""
    redis = FakeRedis()
    breaker = CircuitBreaker(redis)
    await breaker.aclose()
    assert redis.closed is True