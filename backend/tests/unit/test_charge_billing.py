"""W3 任务 3 RED：并发扣减——乐观锁(version) CAS + 失败重试（保护清单，dev-tdd）。

6.3 链路尾段：幂等落库 → 倍率计价 → 乐观锁更新 balances → 失效余额缓存。
对账标准：Stream 事件数 == usage_records 行数零丢失零重复；
余额 == 初始 − Σcost 精确一致。

本测试覆盖（纯 Python + FakeSession/FakeRedis，不启真 DB/Redis）：
- `compute_cost` 倍率计价纯函数（真实 token × 单价，Decimal 精确，缺价方向计 0）
- `charge_balance` 乐观锁 CAS：版本匹配扣减成功 / 版本冲突重读重试 / 重试耗尽抛错 / 无行抛错
- `UsageConsumer.consume` 扩展：落库带 cost + 扣减 balances + DEL 余额缓存 + 幂等不重复扣减
"""

from decimal import Decimal

import pytest

import app.services.usage_events as usage_events
from app.models import ApiKey, Balance, Model, UsageRecord
from app.services.billing import (
    BALANCE_KEY,
    MAX_CHARGE_RETRIES,
    ChargeConflictError,
    charge_balance,
    compute_cost,
)
from tests._fake_db import FakeSession


class FakeRedis:
    """内存流桩 + DELETE 记录（consume 扣减后失效余额缓存断言用）。"""

    def __init__(self):
        self.streams: dict[str, list[dict]] = {}
        self.deleted: list[str] = []

    async def xadd(self, name, fields, *, id_="*", maxlen=None, approximate=True):
        stream = self.streams.setdefault(name, [])
        entry = {"id": len(stream) + 1, "fields": fields}
        stream.append(entry)
        return entry["id"]

    async def delete(self, *keys):
        self.deleted.extend(keys)
        return len(keys)

    def aclose(self):
        pass


def _valid_event(**overrides) -> dict:
    base = {
        "request_id": "11111111-1111-1111-1111-111111111111",
        "api_key_id": 3,
        "channel_id": 1,
        "model": "deepseek-v4-flash-0731",
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "latency_ms": 230,
        "status_code": 200,
    }
    base.update(overrides)
    return base


def _billing_fixtures() -> FakeSession:
    """计价/扣减三件套：api_key(3)→tenant 1 + 模型定价 + 初始余额。"""
    return FakeSession(
        [
            ApiKey(id=3, tenant_id=1, key_hash="h" * 64),
            Model(
                model_name="deepseek-v4-flash-0731",
                input_price=Decimal("0.01"),
                output_price=Decimal("0.02"),
            ),
            Balance(tenant_id=1, balance=Decimal("100.00"), version=0),
        ]
    )


# ── 1) 倍率计价纯函数（真实 token × 单价） ──


def test_compute_cost_math():
    """cost = 输入 token × 进价 + 输出 token × 出价（真实用量，非预检上限）。"""
    cost = compute_cost(
        Decimal("0.01"), Decimal("0.02"), prompt_tokens=12, completion_tokens=5
    )
    assert cost == Decimal("0.22")  # 0.12 + 0.10


def test_compute_cost_decimal_precision():
    """Decimal 精确换算（单价 6 位小数 × token 整数，对账一致前提）。"""
    cost = compute_cost(
        Decimal("0.000123"), Decimal("0.000456"), prompt_tokens=100, completion_tokens=200
    )
    assert cost == Decimal("0.0123") + Decimal("0.0912")


def test_compute_cost_null_price_counts_zero():
    """input/output_price 为 None → 该方向计 0（未定价模型不扣费）。"""
    cost = compute_cost(None, None, prompt_tokens=100, completion_tokens=50)
    assert cost == Decimal("0")


# ── 2) charge_balance：乐观锁(version) CAS + 失败重试 ──


@pytest.mark.asyncio
async def test_charge_balance_success_decrements_and_bumps_version():
    """版本匹配 → 扣减成功：balance 减 cost、version +1。"""
    session = FakeSession([Balance(tenant_id=1, balance=Decimal("100"), version=0)])

    await charge_balance(session, tenant_id=1, cost=Decimal("10"))

    row = session.stored(Balance)[0]
    assert row.balance == Decimal("90")
    assert row.version == 1


@pytest.mark.asyncio
async def test_charge_balance_retries_on_version_conflict(monkeypatch):
    """版本冲突（另一请求已提交）→ 重读最新 version → 重试成功。"""
    session = FakeSession([Balance(tenant_id=1, balance=Decimal("100"), version=0)])
    original = session.execute
    first = {"n": 0}

    async def flaky_execute(stmt):
        if first["n"] == 0:
            # 首次 CAS 前模拟并发请求已提交（version+1）→ 本次 UPDATE 失配 0 行
            session.stored(Balance)[0].version += 1
        first["n"] += 1
        return await original(stmt)

    monkeypatch.setattr(session, "execute", flaky_execute)

    await charge_balance(session, tenant_id=1, cost=Decimal("10"))

    row = session.stored(Balance)[0]
    assert row.balance == Decimal("90")  # 重试后精确扣减一次
    assert row.version == 2  # 并发提交 1 次 + 本次成功 1 次


@pytest.mark.asyncio
async def test_charge_balance_exhausts_retries_raises(monkeypatch):
    """连续冲突（每次 execute 前 version 都被并发改）→ 重试耗尽抛 ChargeConflictError。"""
    session = FakeSession([Balance(tenant_id=1, balance=Decimal("100"), version=0)])
    original = session.execute
    n = {"count": 0}

    async def always_conflict(stmt):
        session.stored(Balance)[0].version += 1  # 永远失配
        n["count"] += 1
        return await original(stmt)

    monkeypatch.setattr(session, "execute", always_conflict)

    with pytest.raises(ChargeConflictError):
        await charge_balance(session, tenant_id=1, cost=Decimal("10"))

    assert n["count"] == MAX_CHARGE_RETRIES  # 恰好重试上限次后放弃


@pytest.mark.asyncio
async def test_charge_balance_no_row_raises():
    """租户无余额行（异常态）→ 抛 ChargeConflictError（避免静默丢钱）。"""
    session = FakeSession()  # balances 表空

    with pytest.raises(ChargeConflictError):
        await charge_balance(session, tenant_id=1, cost=Decimal("10"))


# ── 3) UsageConsumer.consume：落库带 cost + 扣减 + 失效缓存 ──


@pytest.mark.asyncio
async def test_consumer_consume_charges_balance_and_invalidates_cache():
    """consume 落库后：按模型定价扣减租户余额 + DEL Redis 余额缓存。"""
    session = _billing_fixtures()
    redis = FakeRedis()
    producer = usage_events.UsageProducer(redis)
    await producer.emit(_valid_event())  # prompt=12/completion=5 → cost=0.22

    result = await usage_events.UsageConsumer(session, redis).consume()

    assert result == 1
    row = session.stored(UsageRecord)[0]
    assert row.cost == Decimal("0.22")  # 倍率计价已落库
    bal = session.stored(Balance)[0]
    assert bal.balance == Decimal("99.78")  # 100 - 0.22 精确
    assert bal.version == 1
    assert redis.deleted == [BALANCE_KEY.format(tenant_id=1)]  # 扣减后失效缓存


@pytest.mark.asyncio
async def test_consumer_consume_duplicate_does_not_charge_twice():
    """同 request_id 重复事件：幂等不落新行、不重复扣减（余额只减一次）。"""
    session = _billing_fixtures()
    redis = FakeRedis()
    producer = usage_events.UsageProducer(redis)
    ev = _valid_event()
    await producer.emit(ev)
    await producer.emit(dict(ev))  # 同 request_id 重放

    result = await usage_events.UsageConsumer(session, redis).consume()

    assert result == 1
    assert len(session.stored(UsageRecord)) == 1
    assert session.stored(Balance)[0].balance == Decimal("99.78")
    assert redis.deleted == [BALANCE_KEY.format(tenant_id=1)]  # 仅扣减一次 → 缓存只删一次


@pytest.mark.asyncio
async def test_consumer_consume_without_redis_skips_cache_invalidation():
    """redis=None（worker task 无缓存句柄）→ 仍扣减，但跳过失效缓存不报错。"""
    session = _billing_fixtures()
    # 直接调 consume(events)，redis=None：离线流不可用，事件显式传入
    await usage_events.UsageConsumer(session, redis=None, stream="usage:events").consume(
        [_valid_event()]
    )

    assert session.stored(Balance)[0].balance == Decimal("99.78")  # 扣减照常
