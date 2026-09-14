"""W3 任务 2/3：用量事件发射与异步落库（Redis Stream + worker 消费 + 乐观锁扣减）。

链路（PROJECT_CONTEXT 6.3 全段）：网关转发成功后捕获 usage → **XADD
`usage:events`**（Redis Stream 原生存）→ worker 消费组读取 → **幂等落
`usage_records`**（request_id 全局唯一索引兜底，二插零副作用）→ **倍率计价
（compute_cost）→ 乐观锁(version)扣减 balances → 失效余额缓存**。

设计要点：
- `build_usage_event` 纯函数：把转发结果映射到事件 dict。`request_id` 为幂等
  锚点，`total_tokens` 由 prompt+completion 计算得出。
- `UsageProducer.emit`：XADD 到固定流名 `usage:events`。Redis 字段值需为 str
  /bytes → request_id(uuid) 转 str。生产侧再由消费侧靠 request_id 幂等去重。
- `UsageConsumer.consume`：迭代流内事件 → 已存在 request_id 跳过（幂等）→
  插入 `UsageRecord`（cost 由模型定价 × 实际 token 计算）→ 逐条乐观锁扣减
  租户余额 → 扣减成功失效 Redis 余额缓存。生产环境由 `usage_records.request_id`
  unique 兜底，重复消费零重复行；扣减冲突抛 `ChargeConflictError` 使事务回滚、
  事件留流重试（对账「余额 == 初始 − Σcost」精确一致）。
"""

import uuid
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, Model, UsageRecord
from app.services.billing import (
    BALANCE_KEY,
    ChargeConflictError,
    charge_balance,
    compute_cost,
)

# 用量事件流名与消费组名（对齐 PROJECT_CONTEXT 6.3 `usage:{ts}` 语义，统一为事件流）
USAGE_STREAM = "usage:events"
USAGE_GROUP = "usage_group"


def build_usage_event(
    request_id: str | uuid.UUID,
    api_key_id: int,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    channel_id: int | None = None,
    latency_ms: int | None = None,
    status_code: int = 200,
) -> dict:
    """构造用量事件 dict（幂等锚点 + 落库字段全集）。

    request_id 转 str（Redis 字段需 str/bytes）；total_tokens 供对账展示。
    """
    return {
        "request_id": str(request_id),
        "api_key_id": api_key_id,
        "channel_id": channel_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "latency_ms": latency_ms,
        "status_code": status_code,
    }


class UsageProducer:
    """用量事件生产者：把转发后的 usage 事件 XADD 进 Redis Stream。

    持有 Redis 连接（lifespan 构造单例），emit 非阻塞原语式追加，不落库。
    """

    def __init__(self, redis: Redis, stream: str = USAGE_STREAM) -> None:
        self.redis = redis
        self.stream_name = stream

    async def emit(self, event: dict) -> str:
        """把事件 XADD 进 `usage:events`，返回流内条目 id（幂等锚点复用 request_id）。

        Redis XADD 拒绝 None 字段值 → 落流前过滤可空可选字段（channel_id/latency_ms），
        消费端用 `ev.get()` 缺省 None 兼容。request_id/api_key_id/model/tokens 必填保留。
        """
        payload = {k: v for k, v in event.items() if v is not None}
        return await self.redis.xadd(self.stream_name, payload)

    async def aclose(self) -> None:
        """lifespan 退出时关闭 Redis 连接（与 billing/limiter/idempotency 配对）。"""
        await self.redis.aclose()


class UsageConsumer:
    """用量事件消费者：读 Stream → 幂等落 `usage_records`。

    消费侧依赖 session（生产为 DB AsyncSession，测试为离线桩）与已落库的
    request_id 集合做幂等去重；真正的一致性由 `usage_records.request_id`
    unique 索引兜底（重复消费被 DB 拒绝，零重复行）。

    consume 为 async：先查后插（select request_id 判定已存在），与离线桩及
    真实 async session 的 `scalar` 语义一致；落库前 commit。
    """

    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        stream: str = USAGE_STREAM,
    ) -> None:
        self.session = session
        self.redis = redis
        self.stream_name = stream

    async def _existing_request_ids(self) -> set[str]:
        """查询已落库的 request_id 集合（幂等去重判定基准）。"""
        rows = await self.session.scalars(select(UsageRecord.request_id))
        return {str(r) for r in rows.all() if r is not None}

    @staticmethod
    def _as_int(value) -> int | None:
        """把 Stream 字段值收敛为 int 或 None。

        worker 读取 Stream 用 decode_responses=True → 数值字段均为 str（'10'、'1'）；
        落库到 int 列前必须收敛。None/空串 → None（可选字段缺失兼容）。
        """
        if value is None or value == "":
            return None
        return int(value)

    async def _price_for(self, model_name: str) -> tuple[Decimal | None, Decimal | None]:
        """查模型定价（input/output）。模型不存在/未定价 → (None, None)（计 0 不扣费）。"""
        row = await self.session.scalar(
            select(Model).where(Model.model_name == model_name)
        )
        if row is None:
            return None, None
        return row.input_price, row.output_price

    async def _tenant_for(self, api_key_id: int) -> int | None:
        """api_key_id → 租户（扣减 balances 的归属主体）。"""
        row = await self.session.scalar(
            select(ApiKey).where(ApiKey.id == api_key_id)
        )
        return row.tenant_id if row is not None else None

    async def consume(self, events: list[dict] | None = None) -> int:
        """处理一批用量事件（缺省从内存流读），幂等落库 + 乐观锁扣减，返回落库行数。

        离线单测缺省从 FakeRedis 流读；生产由 worker task 传事件并提供 DB
        session。重复 request_id（已有记录）跳过，不新增行也不重复扣减。
        Stream 读出字段经 `_as_int` 收敛：Redis 值恒为 str，int 列绑定前须还原。

        每条新增记录：按模型定价 × 实际 token 计算 cost 落库 → 由 api_key 反查
        租户 → `charge_balance` 乐观锁扣减 → 扣减成功失效 Redis 余额缓存
        （redis 无句柄时跳过失效，扣减照常）。扣减冲突抛错 → 事务回滚 → 事件
        留流由外层重试（对账精确一致，不静默丢钱）。
        """
        events = events if events is not None else self._drain_events()
        existing = await self._existing_request_ids()
        inserted = 0
        for ev in events:
            rid = ev["request_id"]
            if rid in existing:
                continue  # 幂等：已落库，跳过（对齐 request_id unique 语义）
            api_key_id = self._as_int(ev.get("api_key_id"))
            prompt = self._as_int(ev.get("prompt_tokens"))
            completion = self._as_int(ev.get("completion_tokens"))
            in_price, out_price = await self._price_for(ev["model"])
            cost = compute_cost(in_price, out_price, prompt or 0, completion or 0)
            self.session.add(
                UsageRecord(
                    request_id=rid,
                    api_key_id=api_key_id,
                    channel_id=self._as_int(ev.get("channel_id")),
                    model=ev["model"],
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    latency_ms=self._as_int(ev.get("latency_ms")),
                    status_code=self._as_int(ev.get("status_code")),
                    cost=cost,
                )
            )
            existing.add(rid)
            inserted += 1
            # 6.3 尾段：倍率计价已完成 → 乐观锁扣减租户余额 → 失效余额缓存
            tenant_id = (
                await self._tenant_for(api_key_id) if api_key_id is not None else None
            )
            if tenant_id is not None:
                await charge_balance(self.session, tenant_id, cost)
                if self.redis is not None:
                    await self.redis.delete(BALANCE_KEY.format(tenant_id=tenant_id))
        if inserted:
            await self.session.commit()
        return inserted

    def _drain_events(self) -> list[dict]:
        """从内存流取出全部事件字段（离线测试用；生产走 XREADGROUP + XACK）。"""
        if not hasattr(self.redis, "streams"):
            return []
        stream = self.redis.streams.get(self.stream_name, [])
        return [entry["fields"] for entry in stream]