"""计费服务：余额预检（W3 任务 1）+ 定价估算。

链路（PROJECT_CONTEXT 6.3）：鉴权 → 限流 → **余额预检（Redis 缓存，不查库，
不足返 402）** → 转发。本模块暂只做预检与缓存回填；实际扣减 + 失效缓存归
W3 任务 3（乐观锁 CAS 时才 DEL）。

设计要点：
- 余额缓存 key = `bal:{tenant_id}`（对齐 balances 表 tenant_id 主键），TTL 兜底
  防止长时间停留在缓存导致与库漂移——任务 3 扣减后主动失效。
- 缓存命中直接判断（不查库）；未命中才查库回填，保证常态路径预检亚毫秒级。
- `estimate_cost` 纯函数：输入 token 实时估算（estimate_tokens），输出 token
  未知取 MAX_OUTPUT_TOKENS 上限（保守预检，宁多扣不漏）。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InsufficientBalanceError
from app.models import Balance

# Redis 余额缓存键模板（tenant 级）
BALANCE_KEY = "bal:{tenant_id}"
# 预检缓存兜底 TTL（秒）：钱非固定标注，避免长期驻留与库漂移
BALANCE_CACHE_TTL = 60
# 输出 token 未知时的保守估算上限（预检不低估请求成本）
MAX_OUTPUT_TOKENS = 2048


def estimate_cost(
    input_price: Decimal | None,
    output_price: Decimal | None,
    input_tokens: int,
    output_tokens: int = MAX_OUTPUT_TOKENS,
) -> Decimal:
    """预估本次请求成本 = 输入 token × 进价 + 输出上限 × 出价。

    input/outpu_price 为 NULL（未定价）时该方向按 0 计。token 为上界整数，
    用 Decimal 精确换算避免 float 精度漂移（对账一致性前提）。
    """
    return (Decimal(input_price or 0) * Decimal(input_tokens)) + (
        Decimal(output_price or 0) * Decimal(output_tokens)
    )


class BalanceService:
    """余额预检：持有 Redis 连接（lifespan 构造单例），缓存命中不落库。"""

    def __init__(self, redis, ttl: int = BALANCE_CACHE_TTL) -> None:
        self.redis = redis
        self.ttl = ttl

    async def precheck(
        self,
        session: AsyncSession,
        tenant_id: int,
        cost: Decimal,
    ) -> None:
        """余额预检：不足抛 402。缓存命中不查库；未命中查库回填。

        session 由调用方（网关请求作用域）提供，便于单测注入离线桩。
        """
        key = BALANCE_KEY.format(tenant_id=tenant_id)
        cached = await self.redis.get(key)
        if cached is None:
            row = await session.scalar(
                select(Balance).where(Balance.tenant_id == tenant_id)
            )
            balance = row.balance if row and row.balance is not None else Decimal("0")
            await self.redis.set(key, str(balance), ex=self.ttl)
        else:
            balance = Decimal(cached)
        if balance < cost:
            raise InsufficientBalanceError()

    async def aclose(self) -> None:
        """lifespan 退出时关闭 Redis 连接（与 limiter/idempotency 配对）。"""
        await self.redis.aclose()