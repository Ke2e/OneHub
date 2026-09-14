"""W4 任务 1：三态熔断器（保护清单，Redis Lua 手写，禁换库）。

规格（PROJECT_CONTEXT 6.2）：
    failure_count >= N(默认5) → OPEN（记 opened_at）
    now - opened_at >= cooldown(30s) → HALF_OPEN（放行 1 个探测请求）
    探测成功 → CLOSED（failure_count 清零）；失败 → 重新 OPEN

- 状态存 Redis：key = breaker:{channel_id}，HASH 字段 state / failure_count /
  opened_at。多 worker 共享同一熔断状态（与限流同一 redis，EVAL 内
  read-modify-write 原子，并发无竞态）。
- 手写 Lua 脚本封装三态迁移；单测对拍纯函数 `_decide_allow` / `_after_failure`
  / `_after_success`（与令牌桶 _refill 同型对拍，Lua 本体真 Redis 冒烟验证）。

使用方式（路由层）：
    ok = await breaker.can_try(channel_id, now)   # 可路由？(OPEN 冷却期拒绝)
    opened = await breaker.record_failure(channel_id, now)  # 失败计数，达阈值 OPEN
    await breaker.record_success(channel_id)               # 成功清零 / 探测成功 CLOSED
"""

import time

# 失败阈值（默认 5）：CLOSED 下连续失败达此值即 OPEN
THRESHOLD = 5
# 冷却期秒（默认 30）：OPEN 后冷却结束进入 HALF_OPEN 放行探测
COOLDOWN = 30

# Lua：查询 + 三态迁移（HASH breaker:{cid}: state/failure_count/opened_at）
# ARGV[1]=now  ARGV[2]=threshold  ARGV[3]=cooldown
CIRCUIT_BREAKER_LUA = r"""
-- KEYS[1]=breaker:{channel_id}（HASH：state/failure_count/opened_at）
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local cooldown = tonumber(ARGV[3])
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local fc = tonumber(redis.call('HGET', KEYS[1], 'failure_count') or '0')
local opened = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or now)

-- CLOSED：始终放行（计数在失败时处理）
if state == 'closed' then
  return {1, state}
end
-- OPEN：冷却期内拒绝；冷却已过 → HALF_OPEN 放行探测
if state == 'open' then
  if now - opened < cooldown then
    return {0, state}
  end
  redis.call('HSET', KEYS[1], 'state', 'half_open')
  return {1, 'half_open'}
end
-- HALF_OPEN：放行探测请求（保持半开，探测结果决定切回/重开）
redis.call('HSET', KEYS[1], 'state', 'half_open')
return {1, 'half_open'}
"""

# Lua：失败上报（迁移逻辑与 _after_failure 对拍）。
# 只变更 HASH state/failure_count/opened_at；CLOSED 计数达阈值 → OPEN（记 opened_at）。
FAILURE_LUA = r"""
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local fc = tonumber(redis.call('HGET', KEYS[1], 'failure_count') or '0')
if state == 'open' then
  -- OPEN 下失败：保持 OPEN（冷却期由 cooldown 管理，不重复计新阈值）
  redis.call('HSET', KEYS[1], 'failure_count', fc)
  return 0
end
if state == 'half_open' then
  -- HALF_OPEN 探测失败 → 重新 OPEN（记 opened_at 重启冷却）
  redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now)
  redis.call('HSET', KEYS[1], 'failure_count', fc)
  return 1
end
-- CLOSED：计数 +1，达阈值则 OPEN
fc = fc + 1
if fc >= threshold then
  redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now, 'failure_count', fc)
  return 1
end
redis.call('HSET', KEYS[1], 'failure_count', fc)
return 0
"""

# Lua：成功后清零（CLOSED 保持 / HALF_OPEN 探测成功 → CLOSED）。
SUCCESS_LUA = r"""
redis.call('HSET', KEYS[1], 'state', 'closed', 'failure_count', 0)
return 1
"""


def _decide_allow(state: str, opened_at: float, now: float) -> tuple[bool, str]:
    """放行判定纯函数（与 Lua 对拍锚点）：按 CLOSED/OPEN/HALF_OPEN 返回 (放行, 目标态)。"""
    if state == "open":
        if now - opened_at < COOLDOWN:
            return False, "open"
        return True, "half_open"
    return True, state  # CLOSED 或 HALF_OPEN 均放行


def _after_failure(state: str, failure_count: int) -> tuple[str, int]:
    """失败后状态迁移纯函数（与 FAILURE_LUA 对拍）：返回 (新状态, 新计数)。"""
    if state == "open":
        return "open", failure_count
    if state == "half_open":
        return "open", failure_count  # 探测失败 → 重新 OPEN
    fc = failure_count + 1
    if fc >= THRESHOLD:
        return "open", fc
    return "closed", fc


def _after_success(state: str, failure_count: int) -> tuple[str, int]:
    """成功后状态迁移纯函数（与 SUCCESS_LUA 对拍）：探测成功 → CLOSED 清零。"""
    return "closed", 0


class CircuitBreaker:
    """三态熔断器：Redis HASH 状态 + 手写 Lua 原子迁移（保护清单）。

    所有状态变更走 EVAL（read-modify-write 原子），多 worker 共享同一
    breaker:{channel_id} 键，并发请求无竞态（对齐令牌桶设计）。
    """

    def __init__(
        self,
        redis,
        threshold: int = THRESHOLD,
        cooldown: float = COOLDOWN,
        now_fn=time.time,
    ) -> None:
        self._redis = redis
        self._threshold = threshold
        self._cooldown = cooldown
        self._now = now_fn

    def _key(self, channel_id: int) -> str:
        return f"breaker:{channel_id}"

    async def can_try(self, channel_id: int, now: float | None = None) -> bool:
        """请求可否路由到该渠道（CLOSED/HALF_OPEN 或 OPEN 已过冷却 → True）。"""
        now = now if now is not None else self._now()
        allow, _ = await self._redis.eval(
            CIRCUIT_BREAKER_LUA, 1, self._key(channel_id), now, self._threshold, self._cooldown
        )
        return bool(allow)

    async def record_failure(self, channel_id: int, now: float | None = None) -> bool:
        """上报一次失败：CLOSED 计数达阈值或 HALF_OPEN 探测失败 → 返回 True（已 OPEN）。"""
        now = now if now is not None else self._now()
        res = await self._redis.eval(
            FAILURE_LUA, 1, self._key(channel_id), now, self._threshold
        )
        return bool(res)

    async def record_success(self, channel_id: int) -> None:
        """上报成功：探测成功 → CLOSED 清计数；普通成功保持 CLOSED。"""
        await self._redis.eval(SUCCESS_LUA, 1, self._key(channel_id))

    async def aclose(self) -> None:
        """释放底层 Redis 连接（lifespan 退出）。"""
        await self._redis.aclose()