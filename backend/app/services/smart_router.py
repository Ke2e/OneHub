"""W4 任务 1：智能路由编排层——熔断过滤 + 加权挑选 + 指数退避重试。

职责（gateway 的 ROUTE ⑤ 步骤）：
1. 从候选渠道（同一 model 的多行 enabled，各带 channel）过滤不可用：
   - channels.status != healthy（管理员手动封禁）
   - 熔断器 OPEN 且未过冷却期（breaker.can_try=False）
2. 在可用候选中用加权轮询选中一个渠道。
3. 转发失败（可重试错误）→ 熔断器 record_failure + 指数退避（含抖动）后
   重试下一个候选；成功 → record_success。
4. 全部候选失败 → 抛 UpstreamError(503)。

流式重试只在「首个事件产出前」有意义（已下发数据无法回滚）；本层向网关暴露
`forward`（非流式，带重试）与 `pick_channel`（流式入参，透传前单次选择）。
"""

import time
from typing import Awaitable, Callable

from app.core.errors import UpstreamError
from app.services.circuit_breaker import CircuitBreaker
from app.services.router import Candidate, WeightedRobin, exponential_backoff

# 重试上限（候选数之外的总尝试上限，防止候选交替互熔断时无限重试）
MAX_TOTAL_ATTEMPTS = 3


class SmartRouter:
    """路由编排：给定候选 + 转发 callable，熔断过滤 → 加权挑选 → 退避重试。"""

    def __init__(
        self,
        breaker: CircuitBreaker,
        robin: WeightedRobin | None = None,
        max_attempts: int = MAX_TOTAL_ATTEMPTS,
    ) -> None:
        self._breaker = breaker
        self._robin = robin or WeightedRobin()
        self._max = max_attempts
        self._reset_for_state = False

    def _reset_candidates(self, candidates: list[Candidate]) -> list[Candidate]:
        """复制候选，避免污染调用方原列表。"""
        return [Candidate(channel_id=c.channel_id, weight=c.weight, available=c.available) for c in candidates]

    async def _available(
        self, candidates: list[Candidate], now: float
    ) -> list[Candidate]:
        """标注可用性：channels.status 已由调用方置到 candidate.available；
        本层再把熔断器 OPEN 的渠道标为不可用。"""
        out = []
        for c in candidates:
            if not c.available:
                continue
            if await self._breaker.can_try(c.channel_id, now):
                out.append(c)
        return out

    async def pick_channel(
        self, candidates: list[Candidate], now: float | None = None
    ) -> Candidate | None:
        """选一个可路由渠道（流式路径用）；无可用 → None。"""
        now = now if now is not None else time.time()
        avail = await self._available(candidates, now)
        return self._robin.select(avail)

    async def forward(
        self,
        candidates: list[Candidate],
        upstream: Callable[[int], Awaitable],
        now: float | None = None,
    ):
        """非流式转发带重试：加权挑选 → 尝试 → 失败退避重试 → 成功记好。

        upstream(channel_id) 需抛异常表示该渠道转发失败（可重试错误）。
        返回 upstream 的成功结果；全部候选失败抛 UpstreamError(503)。
        """
        now = now if now is not None else time.time()
        pool = self._reset_candidates(candidates)
        attempt = 0
        while attempt < self._max:
            attempt += 1
            chosen = await self.pick_channel(pool, now)
            if chosen is None:
                raise UpstreamError(
                    message="all channels unavailable", status_code=503
                )
            try:
                result = await upstream(chosen.channel_id)
                await self._breaker.record_success(chosen.channel_id)
                return result
            except Exception:
                # 退避 + 记失败；从候选集剔除该渠道避免同轮再次选到
                opened = await self._breaker.record_failure(chosen.channel_id, now)
                pool = [c for c in pool if c.channel_id != chosen.channel_id]
                if not opened and attempt < self._max:
                    delay = exponential_backoff(attempt=attempt)
                    await self._sleep(delay)
                # 若已 OPEN 则无需退避（其他请求也会避让），直接换候选
        raise UpstreamError(message="all channels unavailable", status_code=503)

    async def _sleep(self, delay: float) -> None:
        """可注入的休眠（测试传 0 / patch 为立即返回）。"""
        await __import__("asyncio").sleep(delay)