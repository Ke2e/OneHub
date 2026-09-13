"""W2 任务 4：幂等键（保护清单，Redis 存储手写，禁引第三方幂等库）。

语义（对齐 Stripe Idempotency-Key 思路，适配 LLM 网关）：
- 调用方对「只应生效一次」的请求带 `Idempotency-Key` 头（规范建议 UUID）
- 网关以 (key_id, idempotency_key) 为键缓存**完整非流式响应体**，TTL 窗口 24h
- 同 key 重复请求 → 直接回放缓存响应，不再转发上游（不重复转发、不重复计费）
- 并发同 key → Redis Lua 脚本原子「查缓存 → 占位」：占位得主继续转发，
  其余请求短轮询等结果（得主完成回放 / 得主放弃则接管重试 / 超时 409）

设计决策：
- 仅**非流式**请求参与幂等（流式 SSE 缓存 = 重放整段事件流，成本高收益低；
  带 Idempotency-Key 的流式请求忽略该头直接转发，不缓存不占位）
- 不校验同 key 不同 body（信任调用方以唯一 key 标记唯一请求；严格校验需
  缓存 body hash，与最小实现原则冲突，且 OpenA 生态无此约定）
- 双键设计：
  - idem:{key_id}:{ik}          响应体缓存（存在 = 已完成，可直接回放）
  - idem:{key_id}:{ik}:claim    在途占位（存在 = 已有请求在转发中）
- 占位键 TTL 短（30s 兜底清理崩溃残留）；缓存键 TTL = 24h 幂等窗口
"""

import asyncio
import json
import time
import uuid

from app.core.errors import ConflictError

# 幂等窗口 / 在途占位兜底 / 轮询等待参数
TTL_SECONDS = 24 * 3600
CLAIM_TTL = 30
WAIT_TIMEOUT = 5.0
POLL_INTERVAL = 0.1

IDEMPOTENCY_LUA = """
-- KEYS[1]=idem:{key_id}:{ik}（缓存键）  KEYS[2]=idem:{key_id}:{ik}:claim（占位键）
-- ARGV[1]=claim 占位值  ARGV[2]=claim TTL
-- EVAL 内 read-modify-write 原子：多请求并发同 key 共享同一 redis，
-- 脚本执行期间无并发间隙 → 「查缓存 → 查占位 → 抢占位」三步打包为一次原子操作。
local cached = redis.call('GET', KEYS[1])
if cached then return {'cached', cached} end
local claim = redis.call('GET', KEYS[2])
if claim then return {'claimed', claim} end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
return {'new', ''}
"""


class IdempotencyOutcome:
    """get_or_acquire 三态：缓存回放 / 在途占位 / 本轮得主。"""

    CACHED = "cached"
    CLAIMED = "claimed"
    ACQUIRED = "acquired"


class IdempotencyService:
    """幂等键服务：Lua 原子占位（防并发双转发）+ 响应缓存 + 轮询等待。

    使用方式（端点，非流式且请求带 Idempotency-Key）：
        1. outcome, body = await svc.get_or_acquire(key_id, ik)
           - CACHED：body 即首次响应，直接回放，不转发
           - ACQUIRED：本轮占位成功，继续转发；任何后续失败路径均需 cancel
           - CLAIMED：内部已轮询等得主（回放 / 接管 / 超时 409），不外显
        2. 转发成功 → await svc.complete(key_id, ik, response)
        3. 转发失败 / 被限流挡回 → await svc.cancel(key_id, ik)（释放占位可重试）
    """

    def __init__(
        self,
        redis,
        ttl_seconds: int = TTL_SECONDS,
        claim_ttl: int = CLAIM_TTL,
        wait_timeout: float = WAIT_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._claim_ttl = claim_ttl
        self._wait_timeout = wait_timeout
        self._poll = poll_interval

    def _keys(self, key_id: int, idem_key: str) -> tuple[str, str]:
        base = f"idem:{key_id}:{idem_key}"
        return base, base + ":claim"

    async def get_or_acquire(self, key_id: int, idem_key: str) -> tuple[str, dict | None]:
        """三态入口（见类 docstring）。CLAIMED 分支在内部轮询消化，不外返。"""
        base, claim = self._keys(key_id, idem_key)
        status, value = await self._redis.eval(
            IDEMPOTENCY_LUA, 2, base, claim, str(uuid.uuid4()), self._claim_ttl
        )
        if status == IdempotencyOutcome.CACHED:
            return IdempotencyOutcome.CACHED, json.loads(value)
        if status == IdempotencyOutcome.CLAIMED:
            return await self._wait_result(base, claim)
        return IdempotencyOutcome.ACQUIRED, None

    async def _wait_result(self, base: str, claim: str) -> tuple[str, dict | None]:
        """在途请求等待：得主写完缓存 → 回放；得主放弃（claim 消失）→ 本请求接管转发；
        超时 → 409（冲突语义，调用方可重试，重新占位走全流程）。"""
        deadline = time.monotonic() + self._wait_timeout
        while True:
            payload = await self._redis.get(base)
            if payload is not None:
                return IdempotencyOutcome.CACHED, json.loads(payload)
            if await self._redis.get(claim) is None:
                # 得主失败并已释放占位（cancel）→ 占位消失且无缓存 → 接管转发
                return IdempotencyOutcome.ACQUIRED, None
            if time.monotonic() >= deadline:
                raise ConflictError("duplicate request still in progress")
            await asyncio.sleep(self._poll)

    async def complete(self, key_id: int, idem_key: str, payload: dict) -> None:
        """得主转发成功：写缓存（覆写，EX=幂等窗口）+ 删除占位。"""
        base, claim = self._keys(key_id, idem_key)
        await self._redis.set(base, json.dumps(payload, ensure_ascii=False), ex=self._ttl)
        await self._redis.delete(claim)

    async def cancel(self, key_id: int, idem_key: str) -> None:
        """得主转发失败 / 被 429 挡回：删除占位，不写缓存 → 后续请求可重新占位重试。"""
        _, claim = self._keys(key_id, idem_key)
        await self._redis.delete(claim)

    async def aclose(self) -> None:
        """释放底层 Redis 连接（lifespan 退出）。"""
        await self._redis.aclose()