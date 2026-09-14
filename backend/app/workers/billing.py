"""Celery worker：异步消费用量 Stream → 幂等落 usage_records（W3 任务 2）。

链路：网关 XADD `usage:events` → Celery worker（redis broker）周期性触发
`process_usage_events` task → 读取 Stream 事件 → `UsageConsumer` 幂等落库
（request_id unique 兜底）→ 对账零重复。乐观锁扣减 balances 归任务 3，
本 task 只做落库。

依赖注入：Celery task 同步签名内用 `asyncio` 桥接 async 引擎/session，
复用 app.core.config 的 DATABASE_URL（与 api 容器一致），不引 FastAPI。
"""

import asyncio

from celery import Celery

from app.core.config import get_settings
from app.services.usage_events import USAGE_GROUP, USAGE_STREAM, UsageConsumer

settings = get_settings()

# Celery app：redis 作为 broker/backend（复用项目 REDIS_URL，W2 ADR-0001 依赖已有）
celery_app = Celery(
    "onehub",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# 单次拉取上限：避免单次消费过多事件挤压内存/事务窗口
_MAX_BATCH = 500
# 消费者名（流消费组内唯一，多 worker 并发读不重复处理）
_CONSUMER = "billing-worker"


@celery_app.task(bind=True, max_retries=2, default_retry_delay=3)
def process_usage_events(self, batch: list[dict]) -> int:
    """消费一批用量事件并幂等落库，返回成功落库行数。

    幂等由 usage_records.request_id unique 兜底：重复事件（XACK 重放/同 request_id
    重发）被 DB unique 拒绝，此处先查后插再 commit，零重复行。失败重试（max_retries=2）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> int:
        async with Session() as session:
            consumer = UsageConsumer(session=session, redis=None, stream=USAGE_STREAM)
            return await consumer.consume(batch)

    try:
        inserted = asyncio.run(_run())
        return inserted
    finally:
        # 单次任务内临时引擎用完即销（worker 长驻进程不持有跨任务连接）
        asyncio.run(engine.dispose())


async def read_and_process(batch_size: int = _MAX_BATCH) -> int:
    """从 usage:events 流消费组拉取一批事件 → 幂等落库 → XACK，返回落库行数。

    生产链路（全程异步）：XREADGROUP 读未处理事件 → UsageConsumer.consume 落库
    → XACK 移交已处理。幂等落库保证重复消费（崩溃后重读）零重复行。
    """
    import redis.asyncio as aioredis

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # 确保消费组存在（幂等创建；Stream 为空时用 XADD 占位再删）
        try:
            await redis.xgroup_create(USAGE_STREAM, USAGE_GROUP, id="0", mkstream=True)
        except Exception:
            pass  # 已存在则忽略
        # 拉取未确认事件（block=0 阻塞直到有事件，读上限 batch_size）
        raw = await redis.xreadgroup(
            USAGE_GROUP, _CONSUMER, {USAGE_STREAM: ">"}, count=batch_size, block=0
        )
        if not raw:
            return 0
        entries = raw[0][1]  # [(msg_id, {field: value}), ...]
        events = [dict(fields) for _, fields in entries]
        async with Session() as session:
            consumer = UsageConsumer(session=session, redis=None, stream=USAGE_STREAM)
            inserted = await consumer.consume(events)
        # 落库成功 → XACK（groupId, consumer, stream, *ids）
        await redis.xack(USAGE_STREAM, USAGE_GROUP, *[mid for mid, _ in entries])
        return inserted
    finally:
        await redis.aclose()
        await engine.dispose()