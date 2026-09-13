"""W2 任务 3：令牌桶限流（保护清单，Redis Lua 手写，禁换库）+ Semaphore 并发限流。

规格（PROJECT_CONTEXT 6.1）：单 Key RPM/TPM 双维度，Redis EVAL 脚本原子执行
（并发请求共享同一 redis，脚本内 read-modify-write 原子，无竞态）。
- KEYS[1] = rate:{key_id}:{dim}，ARGV = capacity, rate/s, now, requested
- 有 token → 消耗并返回 {1, 0}（放行）；无 token → 返回 {0, wait}（wait=Retry-After 秒）
- 全局并发上限：asyncio.Semaphore（进程内；跨进程由多实例各持有一致收敛由 W4 熔断/路由治理）

TPM 维度的 requested 在转发前只能粗估（token 估算），真实 usage 计费由 W3 负责。
"""

import time

from app.schemas.chat import Message

TOKEN_BUCKET_LUA = """
-- KEYS[1]=rate:{key_id}:{dim}  ARGV=capacity, rate/s, now, requested
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local t = tonumber(redis.call('HGET', KEYS[1], 't') or ARGV[1])
local ts = tonumber(redis.call('HGET', KEYS[1], 'ts') or ARGV[3])
t = math.min(capacity, t + (now - ts) * rate)
if t >= requested then
  redis.call('HSET', KEYS[1], 't', t - requested, 'ts', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  return {1, 0}
end
redis.call('HSET', KEYS[1], 't', t, 'ts', now)
local wait = 0
if rate > 0 then
  wait = math.max(1, math.ceil((requested - t) / rate))
end
return {0, wait}
"""


def _refill(current: float, last_ts: float, rate: float, capacity: float, now: float) -> float:
    """补 token（与 Lua 同公式，供单测对拍）：封顶 capacity，时钟倒退不扣减。"""
    return min(capacity, current + max(0.0, now - last_ts) * rate)


def estimate_tokens(messages: list[Message]) -> int:
    """转发前 token 粗估（TPM 维度输入，真实 usage 由 W3 计费）：字符数 // 4，至少 1。"""
    total = sum(len(m.content or "") for m in messages)
    return max(1, total // 4)


class TokenBucket:
    """单维度令牌桶：携带 ARGV 组装 + 返回值解析的 Lua EVAL 封装（脚本本体手写）。"""

    def __init__(self, redis, script: str = TOKEN_BUCKET_LUA) -> None:
        self._redis = redis
        self._script = script

    async def allow(
        self,
        bucket: str,
        capacity: int,
        rate: float,
        requested: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        """消耗 requested 个 token；返回 (是否放行, 拒绝时 Retry-After 秒)。"""
        now = now if now is not None else time.time()
        allowed, retry_after = await self._redis.eval(
            self._script, 1, bucket, capacity, rate, now, requested
        )
        return bool(allowed), int(retry_after)


class RateLimiter:
    """网关面限流：RPM/TPM 双重令牌桶（Redis 共享态）+ 进程内并发上限。

    调用顺序（端点）：check() 桶判定 → try_acquire() 并发槽 → 转发 →
     finally release()。桶拒绝或槽占满均 → 429 + Retry-After。

    并发槽用自维护计数实现：asyncio 单线程协作调度下，try_acquire 的
    「检查 → 自增」之间没有 await 间隙，不会被并发协程打断——等价于
    asyncio.Semaphore 的 try-acquire 语义（Semaphore 本身不提供非阻塞原语）。
    """

    def __init__(self, redis, max_concurrent: int = 16) -> None:
        self._bucket = TokenBucket(redis)
        self._max_concurrent = max_concurrent
        self._in_flight = 0
        self._redis = redis

    async def check(
        self,
        key_id: int,
        rpm_limit: int,
        tpm_limit: int,
        tokens: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        """双维度合并判定：RPM requested=1，TPM requested=估算 token。

        任一拒绝 → (False, wait) 短路返回（RPM 失败不再消耗 TPM 桶）。
        """
        rpm_ok, rpm_wait = await self._bucket.allow(
            f"rate:{key_id}:rpm", rpm_limit, rpm_limit / 60.0, 1, now
        )
        if not rpm_ok:
            return False, rpm_wait
        if tpm_limit > 0:
            tpm_ok, tpm_wait = await self._bucket.allow(
                f"rate:{key_id}:tpm", tpm_limit, tpm_limit / 60.0, tokens, now
            )
            if not tpm_ok:
                return False, tpm_wait
        return True, 0

    def try_acquire(self) -> bool:
        """非阻塞拿并发槽：占用达上限直接拒绝，否则计数 +1。"""
        if self._in_flight >= self._max_concurrent:
            return False
        self._in_flight += 1
        return True

    def release(self) -> None:
        """转发结束（成功/异常/流式断连）释放槽位；端点 finally 保证配对。"""
        self._in_flight -= 1

    async def aclose(self) -> None:
        """释放底层 Redis 连接（lifespan 退出）。"""
        await self._redis.aclose()