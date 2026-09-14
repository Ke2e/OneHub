"""W3 任务 3 对账脚本：乐观锁并发扣减 + 幂等落库（PROJECT_CONTEXT 6.3 对账标准）。

用法（docker compose up -d 后，本机）：
  .\\.venv\\Scripts\\python.exe scripts\\reconcile_charge.py
（脚本默认连 localhost pg + Redis db15 隔离；可用 DATABASE_URL/REDIS_URL 环境变量覆盖）

对账标准（6.3）：50 并发请求后，Stream 事件数 == usage_records 行数，零丢失零重复；
余额 == 初始 − Σcost 精确一致。本脚本按 6.3 语义验证三段：
① 幂等落库零重复：XADD N 唯一事件 → 消费落库 N 行，Stream 事件数 == 行数；
② 倍率计价 + 乐观锁扣减：余额 == 初始 − Σ(usage_records.cost) 精确一致；
③ 并发扣减收敛：asyncio.gather 并发 charge_balance（版本冲突重试路径）→ 余额精确。
额外验证幂等重放：同 request_id 重复事件 → 零新增行、余额不变。
"""

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# 本机跑必须覆盖 .env 的容器主机名（pg/redis）；用户在外部已设则尊重（setdefault）
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://onehub:onehub@localhost:5432/onehub"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

# 脚本位于仓库根 scripts/ 下，将 backend 加入 sys.path 才能导入 app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import redis.asyncio as aioredis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import ApiKey, Balance, Model, Tenant, UsageRecord
from app.services.billing import BALANCE_KEY, charge_balance
from app.services.usage_events import (
    USAGE_STREAM,
    UsageProducer,
    build_usage_event,
)
from app.workers.billing import read_and_process

# ── 测试常量（固定命名保证幂等可重跑） ──
TENANT_NAME = "reconcile-tenant"
MODEL_NAME = "reconcile-model"
KEY_PREFIX = "recon"
KEY_HASH = "a" * 64  # 对账测试专用占位哈希（不落真实密钥）
# 定价 2 位小数 × 整数 token → cost 精确到分，余额 4 位小数对账无舍入
INPUT_PRICE = Decimal("0.010000")
OUTPUT_PRICE = Decimal("0.020000")
INIT_BALANCE = Decimal("1000.0000")
N = 50  # 6.3 对账标准：50 并发请求


def _ok(label: str) -> None:
    print(f"  [reconcile] {label} → PASS")


async def _prepare(session, redis) -> tuple[int, int]:
    """清理旧数据 → 建租户/余额/模型/api_key，返回 (tenant_id, api_key_id)。"""
    # 清理本脚本测试数据（幂等可重跑）
    await session.execute(delete(UsageRecord).where(UsageRecord.model == MODEL_NAME))
    await session.execute(delete(ApiKey).where(ApiKey.key_hash == KEY_HASH))
    await session.execute(delete(Balance).where(Balance.balance < Decimal("-1")))
    # 删旧租户（连带其余额）
    old_t = await session.scalar(select(Tenant).where(Tenant.name == TENANT_NAME))
    if old_t is not None:
        await session.execute(delete(Balance).where(Balance.tenant_id == old_t.id))
        await session.delete(old_t)
    await session.execute(delete(Model).where(Model.model_name == MODEL_NAME))
    await session.commit()
    # 重建
    tenant = Tenant(name=TENANT_NAME)
    session.add(tenant)
    await session.flush()
    session.add(Balance(tenant_id=tenant.id, balance=INIT_BALANCE, version=0))
    api_key = ApiKey(
        tenant_id=tenant.id,
        name="reconcile-key",
        key_prefix=KEY_PREFIX,
        key_hash=KEY_HASH,
    )
    session.add(api_key)
    await session.flush()
    session.add(
        Model(
            model_name=MODEL_NAME,
            input_price=INPUT_PRICE,
            output_price=OUTPUT_PRICE,
            enabled=True,
        )
    )
    await session.commit()
    # 清空事件流（连同消费组，read_and_process 会幂等重建）
    await redis.delete(USAGE_STREAM)
    return tenant.id, api_key.id


async def _sum_cost(session, api_key_id: int) -> Decimal:
    """Σ usage_records.cost（本 api_key 全部记录，Decimal 精确求和）。"""
    total = await session.scalar(
        select(func.sum(UsageRecord.cost)).where(UsageRecord.api_key_id == api_key_id)
    )
    return Decimal(total or 0)


async def _balance_of(session, tenant_id: int) -> Decimal:
    row = await session.scalar(select(Balance).where(Balance.tenant_id == tenant_id))
    return row.balance if row is not None else Decimal("0")


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    print(f"[reconcile] 连接 pg={settings.database_url.rsplit('@', 1)[-1]} redis={settings.redis_url}")

    try:
        async with Session() as session:
            tenant_id, api_key_id = await _prepare(session, redis)
            print(
                f"[reconcile] 准备：tenant={TENANT_NAME}(id={tenant_id}) "
                f"api_key(id={api_key_id}) 模型={MODEL_NAME} 初始余额={INIT_BALANCE}"
            )
            init_balance = await _balance_of(session, tenant_id)

        # ── ① 幂等落库零重复：XADD N 唯一事件 → 消费 → 行数 == 事件数 ──
        producer = UsageProducer(redis)
        events = [
            build_usage_event(
                request_id=str(uuid.uuid4()),
                api_key_id=api_key_id,
                model=MODEL_NAME,
                prompt_tokens=10 + i,
                completion_tokens=5 + (i % 7),
            )
            for i in range(N)
        ]
        for ev in events:
            await producer.emit(ev)
        stream_len = await redis.xlen(USAGE_STREAM)
        inserted = await read_and_process()
        async with Session() as session:
            row_count = (
                await session.scalar(
                    select(func.count()).select_from(UsageRecord).where(
                        UsageRecord.model == MODEL_NAME
                    )
                )
            )
            distinct = (
                await session.scalar(
                    select(func.count(func.distinct(UsageRecord.request_id))).where(
                        UsageRecord.model == MODEL_NAME
                    )
                )
            )
            assert stream_len == N == inserted == row_count == distinct, (
                f"Stream={stream_len} 落库={inserted} 行数={row_count} 去重={distinct}"
            )
            _ok(
                f"① 幂等落库：Stream 事件数={stream_len} == usage_records 行数="
                f"{row_count}（唯一 request_id 去重={distinct}，零丢失零重复）"
            )

            # ── ② 倍率计价 + 乐观锁扣减：余额 == 初始 − Σcost ──
            sum_cost = await _sum_cost(session, api_key_id)
            balance = await _balance_of(session, tenant_id)
            assert balance == init_balance - sum_cost, (
                f"余额={balance} 期望={init_balance}−{sum_cost}={init_balance - sum_cost}"
            )
            _ok(
                f"② 乐观锁扣减：余额={balance} == 初始 {init_balance} − Σcost="
                f"{sum_cost}（精确一致）"
            )

            # ── 幂等重放：同 request_id 重复事件 → 零新增、余额不变 ──
            balance_before = balance
            await producer.emit(dict(events[0]))  # 复用首个 request_id
            replayed = await read_and_process()
            row_count2 = (
                await session.scalar(
                    select(func.count()).select_from(UsageRecord).where(
                        UsageRecord.model == MODEL_NAME
                    )
                )
            )
            balance_after = await _balance_of(session, tenant_id)
            assert replayed == 0 and row_count2 == N and balance_after == balance_before, (
                f"重放落库={replayed} 行数={row_count2} 余额={balance_after}"
            )
            _ok(
                f"幂等重放：同 request_id 重复事件落库 {replayed} 新行、余额不变"
                f"（{balance_after}，DB unique 兜底零重复扣减）"
            )

        # ── ③ 并发扣减收敛：50 并发 charge_balance（版本冲突重试路径） ──
        pool_engine = create_async_engine(
            settings.database_url, pool_pre_ping=True, pool_size=N, max_overflow=0
        )
        PoolSession = async_sessionmaker(pool_engine, expire_on_commit=False)
        costs = [Decimal("1.00")] * N
        async with Session() as session:
            before = await _balance_of(session, tenant_id)

        async def _charge_one(cost: Decimal) -> None:
            async with PoolSession() as s:
                await charge_balance(s, tenant_id, cost)
                await s.commit()

        await asyncio.gather(*[_charge_one(c) for c in costs])
        expected = before - sum(costs)
        async with Session() as session:
            final = await _balance_of(session, tenant_id)
        assert final == expected, f"并发扣减后余额={final} 期望={expected}"
        _ok(
            f"③ 并发扣减：{N} 路 asyncio.gather 并发 charge_balance（乐观锁版本冲突"
            f"重试）→ 余额={final} == {before} − {sum(costs)}（零丢失零重复扣减）"
        )
        await pool_engine.dispose()

        print("[reconcile] ALL PASS —— 对账脚本通过（PROJECT_CONTEXT 6.3 标准）")
        return 0
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
