"""计费服务：余额预检（W3 任务 1）+ 定价估算 + 乐观锁并发扣减（W3 任务 3）。

链路（PROJECT_CONTEXT 6.3）：鉴权 → 限流 → **余额预检（Redis 缓存，不查库，
不足返 402）** → 转发 → XADD 用量事件 → worker 幂等落库 + **倍率计价 →
乐观锁(version)更新 balances → 失效余额缓存**。

设计要点：
- 余额缓存 key = `bal:{tenant_id}`（对齐 balances 表 tenant_id 主键），TTL 兜底
  防止长时间停留在缓存导致与库漂移——扣减成功后主动失效。
- 缓存命中直接判断（不查库）；未命中才查库回填，保证常态路径预检亚毫秒级。
- `estimate_cost` 纯函数：输入 token 实时估算（estimate_tokens），输出 token
  未知取 MAX_OUTPUT_TOKENS 上限（保守预检，宁多扣不漏）。
- `compute_cost`（任务 3 倍率计价）：实际 prompt/completion token × 单价，
  与预检估算（上限）不同——预检拦不足，落库按真实用量计费。
- `charge_balance`：乐观锁(version) CAS 扣减（保护清单，手写）——先读最新
  version → UPDATE ... WHERE version = 读到的版本，rowcount=0 说明并发已提交
  → 重读重试；无余额行/重试耗尽抛 ChargeConflictError（交外层重试/告警，
  不静默丢钱，保证对账「余额 == 初始 − Σcost 精确一致」）。
"""

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InsufficientBalanceError
from app.models import Balance

# Redis 余额缓存键模板（tenant 级）
BALANCE_KEY = "bal:{tenant_id}"
# 预检缓存兜底 TTL（秒）：钱非固定标注，避免长期驻留与库漂移
BALANCE_CACHE_TTL = 60
# 输出 token 未知时的保守估算上限（预检不低估请求成本）
MAX_OUTPUT_TOKENS = 2048
# 乐观锁扣减版本冲突重试上限。冲突是瞬态：期间有并发请求已提交，重读最新
# version 即收敛。生产 worker 并发低（compose concurrency=2，冲突几乎为零）；
# 上限按对账脚本 50 路真并发验证设计——最坏情况下同波并发者每波仅 1 人读到
# 未被消费的 version，第 k 个并发者需约 k 次重试，取 100 覆盖 50 并发 + 余量。
MAX_CHARGE_RETRIES = 100


class ChargeConflictError(RuntimeError):
    """乐观锁扣减无法收敛（无余额行 / 版本冲突重试耗尽）：worker 回滚重试/告警。

    属内部错误（发生在 worker 消费进程，非 HTTP 层），不映射 OpenAI 错误出口。
    """


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


def compute_cost(
    input_price: Decimal | None,
    output_price: Decimal | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal:
    """倍率计价（6.3 幂等落库后的真实计费）：实际 token × 单价。

    与 `estimate_cost` 的区别：completion_tokens 为实际值而非上限（预检拦截
    不足，落库按真实用量收费）。未定价方向按 0 计。Decimal 精确换算。
    """
    return (Decimal(input_price or 0) * Decimal(prompt_tokens)) + (
        Decimal(output_price or 0) * Decimal(completion_tokens)
    )


async def charge_balance(
    session: AsyncSession,
    tenant_id: int,
    cost: Decimal,
) -> None:
    """乐观锁(version)扣减租户余额：CAS 失配重读重试，耗尽/无行抛 ChargeConflictError。

    先读最新 balance/version → `UPDATE balances SET balance = balance - cost,
    version = version + 1 WHERE tenant_id = ? AND version = ?`——单行 UPDATE 在
    事务内行级互斥，等价 read-modify-write 原子：每个 version 恰被一个并发
    请求消费，并发扣减零丢失。rowcount=0 说明期间已有并发提交（version 已变）
    → 重读重试（乐观锁失败重试）；无余额行（数据异常态）与重试耗尽都抛错，
    由外层回滚让事件留流重试，保证「余额 == 初始 − Σcost 精确一致」。
    """
    for _ in range(MAX_CHARGE_RETRIES):
        row = await session.scalar(
            select(Balance).where(Balance.tenant_id == tenant_id)
        )
        if row is None:
            raise ChargeConflictError(f"tenant {tenant_id} has no balance row")
        result = await session.execute(
            update(Balance)
            .where(Balance.tenant_id == tenant_id, Balance.version == row.version)
            .values(balance=row.balance - cost, version=row.version + 1)
        )
        if result.rowcount == 1:
            return
    raise ChargeConflictError(
        f"tenant {tenant_id} charge conflict after {MAX_CHARGE_RETRIES} retries"
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