"""W2 任务 3 RED：令牌桶（Redis Lua 手写，保护清单）+ Semaphore 并发限流单测。

策略：
- Lua 脚本本体只在真机验证（redis EVAL），单测注入 fake eval 收取 ARGV / 回放返回值，
  覆盖 Python 侧全部逻辑：ARGV 组装、返回值解析、Retry-After 透传、双维度合并、Semaphore、token 估算。
- `_refill` 纯函数（与 Lua 同一补 token 公式）作数学对拍锚点，防公式回退。
"""

import asyncio

import pytest

from app.schemas.chat import ChatCompletionRequest, Message
from app.services.rate_limit import (
    RateLimiter,
    TOKEN_BUCKET_LUA,
    TokenBucket,
    _refill,
    estimate_tokens,
)


class FakeRedis:
    """收集 eval 调用 + 可编程返回值的桩（对齐 _fake_db 离线风格）。"""

    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.closed = False

    async def eval(self, script, numkeys, *args):
        self.calls.append((script, numkeys, args))
        return self.result

    async def aclose(self):
        self.closed = True


# ── _refill 纯函数（与 Lua 公式对拍） ──

def test_refill_bounded_by_capacity():
    """补 token 封顶 capacity（超过部分丢弃）。"""
    assert _refill(current=90.0, last_ts=100.0, rate=10.0, capacity=100.0, now=200.0) == 100.0


def test_refill_partial_proportional():
    """补 token = 已存 + 经过秒数 * 速率。"""
    assert _refill(current=40.0, last_ts=100.0, rate=10.0, capacity=100.0, now=106.0) == 100.0
    assert _refill(current=0.0, last_ts=100.0, rate=2.0, capacity=100.0, now=103.0) == 6.0


def test_refill_clock_rewind_no_decrease():
    """时钟倒退不扣减（now < last_ts 按 0 秒处理）。"""
    assert _refill(current=50.0, last_ts=200.0, rate=10.0, capacity=100.0, now=150.0) == 50.0


def test_refill_first_use_starts_empty():
    """首次使用：t 缺省 0、ts 缺省 now → 重建后立即补 rate*(now-now)=0。"""
    assert _refill(current=0.0, last_ts=123.0, rate=5.0, capacity=100.0, now=123.0) == 0.0


# ── TokenBucket：Lua EVAL 封装 ──

def test_bucket_allow_args_and_result():
    """放行：ARGV=[capacity, rate/s, now, requested]，返回 [1, 0] → (True, 0)。"""
    redis = FakeRedis(result=[1, 0])
    bucket = TokenBucket(redis)
    allowed, wait = asyncio.run(bucket.allow("rate:1:rpm", capacity=60, rate=1.0, requested=1, now=1000.0))
    assert (allowed, wait) == (True, 0)
    script, numkeys, args = redis.calls[0]
    assert script == TOKEN_BUCKET_LUA
    assert numkeys == 1
    assert args == ("rate:1:rpm", 60.0, 1.0, 1000.0, 1.0)


def test_bucket_deny_returns_retry_after():
    """拒绝：Lua 返回 [0, wait] → (False, wait)。"""
    redis = FakeRedis(result=[0, 7])
    bucket = TokenBucket(redis)
    allowed, wait = asyncio.run(bucket.allow("rate:1:rpm", capacity=60, rate=1.0, requested=1, now=1000.0))
    assert (allowed, wait) == (False, 7)


def test_bucket_casts_result_types():
    """Lua 返回 {1,0} 被 redis-py 解析为 [1, 0]（int）→ 统一 cast bool/int。"""
    redis = FakeRedis(result=[1, 0])
    bucket = TokenBucket(redis)
    allowed, wait = asyncio.run(bucket.allow("rate:1:rpm", capacity=1, rate=1.0, requested=1))
    assert allowed is True
    assert wait == 0


# ── RateLimiter：双维度合并 ──

async def _limiter_with(redis: FakeRedis) -> RateLimiter:
    return RateLimiter(redis)


def test_check_both_dims_allow():
    """RPM 与 TPM 都放行 → (True, 0)，且按 key_id 分桶两次 eval。"""
    redis = FakeRedis(result=[1, 0])
    limiter = RateLimiter(redis)
    allowed, wait = asyncio.run(limiter.check(key_id=7, rpm_limit=60, tpm_limit=10000, tokens=100))
    assert (allowed, wait) == (True, 0)
    keys = [c[2][0] for c in redis.calls]
    assert keys == ["rate:7:rpm", "rate:7:tpm"]
    # RPM requested=1；TPM requested=估算 token
    assert redis.calls[0][2][-1] == 1.0
    assert redis.calls[1][2][-1] == 100.0


def test_check_rpm_deny_stops_before_tpm():
    """RPM 拒绝 → 直接 (False, wait)，不再检查 TPM（短路，双桶互不消耗）。"""
    redis = FakeRedis(result=[0, 5])
    limiter = RateLimiter(redis)
    allowed, wait = asyncio.run(limiter.check(key_id=7, rpm_limit=60, tpm_limit=10000, tokens=100))
    assert (allowed, wait) == (False, 5)
    assert len(redis.calls) == 1


def test_check_tpm_deny_after_rpm_allow():
    """RPM 放行 + TPM 拒绝 → (False, TPM wait)。"""
    redis = FakeRedis(result=[1, 0])
    limiter = RateLimiter(redis)
    # 第二次 eval（TPM）返回拒绝
    async def deny_once(*a, **k):
        redis.calls.append(a)
        return [0, 9] if len(redis.calls) == 2 else [1, 0]

    redis.eval = deny_once
    allowed, wait = asyncio.run(limiter.check(key_id=7, rpm_limit=60, tpm_limit=10000, tokens=100))
    assert (allowed, wait) == (False, 9)


def test_check_zero_tpm_skips_dim():
    """tpm_limit=0（未配置）→ 只查 RPM。"""
    redis = FakeRedis(result=[1, 0])
    limiter = RateLimiter(redis)
    allowed, wait = asyncio.run(limiter.check(key_id=7, rpm_limit=60, tpm_limit=0, tokens=100))
    assert (allowed, wait) == (True, 0)
    assert len(redis.calls) == 1


# ── Semaphore 并发上限 ──

def test_semaphore_limits_concurrency():
    """并发槽：max_concurrent=2 时第 3 次 try_acquire 拒绝；release 后可再次取得。"""
    limiter = RateLimiter(FakeRedis(), max_concurrent=2)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False  # 并发满
    limiter.release()
    assert limiter.try_acquire() is True  # 释放后可再进


def test_semaphore_acquire_release_balance():
    """acquire 后必须 release 对应的次数（超出 release 会在并发测试暴露）。"""
    limiter = RateLimiter(FakeRedis(), max_concurrent=1)
    assert limiter.try_acquire() is True
    limiter.release()
    assert limiter.try_acquire() is True


def test_aclose_closes_redis():
    """aclose → 底层 redis 连接关闭（lifespan 退出时调用）。"""
    redis = FakeRedis()
    limiter = RateLimiter(redis)
    asyncio.run(limiter.aclose())
    assert redis.closed is True


# ── token 估算（TPM 维度输入） ──

def test_estimate_tokens_min_one():
    """空/无 content 消息 → 至少 1（避免 TPM 桶 requested=0 失去限流意义）。"""
    req = ChatCompletionRequest(model="m", messages=[Message(role="user")])
    assert estimate_tokens(req.messages) == 1


def test_estimate_tokens_proportional_to_chars():
    """token 粗估 = 字符数 // 4（中文/英文混合粗口径），len 100 → 25。"""
    req = ChatCompletionRequest(model="m", messages=[Message(role="user", content="x" * 100)])
    assert estimate_tokens(req.messages) == 25


def test_estimate_tokens_sums_all_messages():
    """多消息累加。"""
    req = ChatCompletionRequest(
        model="m",
        messages=[Message(role="user", content="a" * 40), Message(role="assistant", content="b" * 44)],
    )
    assert estimate_tokens(req.messages) == 21