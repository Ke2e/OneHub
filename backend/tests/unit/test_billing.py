"""W3 任务 1 RED：余额预检 + 计费定价估算。

策略（对齐限流测试双形态定式）：
- `estimate_cost` 纯函数作数学锚点（输入估 x 进价 + 输出上限 x 出价）。
- Lua/eval 无关，BalanceService.precheck 直接 GET 缓存 → 未命中查库回填 → 判断；
  单测注入 FakeRedis（get/set 桩）+ FakeSession 离线覆盖分支，触不到真实 DB/Redis。
- 402 错误结构独立断言（与 OpenAI 错误出口契约对齐）。
"""

from decimal import Decimal

import pytest

from app.core.errors import InsufficientBalanceError, _body
from app.models import Balance
from app.services.billing import (
    BALANCE_CACHE_TTL,
    BALANCE_KEY,
    MAX_OUTPUT_TOKENS,
    BalanceService,
    estimate_cost,
)
from tests._fake_db import FakeSession


class FakeRedis:
    """GET/SET 桩：收集 set 写入（key/value/ttl）+ 可编程初值（对齐离线风格）。"""

    def __init__(self, initial: dict | None = None):
        self._store = dict(initial or {})
        self.sets: list[tuple] = []
        self.closed = False

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None):
        self._store[key] = value
        self.sets.append((key, value, ex))

    async def aclose(self):
        self.closed = True


# ── 402 错误结构 ──

def test_insufficient_balance_error_shape():
    """402 余额不足：OpenAI 结构 error_type=insufficient_quota / code=insufficient_balance。"""
    err = InsufficientBalanceError()
    assert err.status_code == 402
    assert err.error_type == "insufficient_quota"
    assert err.code == "insufficient_balance"
    assert err.message == "insufficient balance"
    body = _body(err.message, err.error_type, err.param, err.code)
    assert body["error"] == {
        "message": "insufficient balance",
        "type": "insufficient_quota",
        "param": None,
        "code": "insufficient_balance",
    }


# ── estimate_cost 纯函数（与定价公式对拍） ──

def test_estimate_cost_math():
    """成本 = 输入 token x 进价 + 输出上限 token x 出价。"""
    cost = estimate_cost(Decimal("0.01"), Decimal("0.02"), input_tokens=100, output_tokens=100)
    assert cost == Decimal("3.00")  # 1.00 + 2.00


def test_estimate_cost_default_output_cap():
    """未给输出 token → 走 MAX_OUTPUT 上限（保守预检）。"""
    cost = estimate_cost(Decimal("1"), Decimal("2"), input_tokens=0)
    assert cost == Decimal("2") * Decimal(MAX_OUTPUT_TOKENS)


def test_estimate_cost_null_price_counts_zero():
    """input_price/output_price 为 None → 该方向不计费。"""
    cost = estimate_cost(None, None, input_tokens=100, output_tokens=50)
    assert cost == Decimal("0")


# ── precheck：缓存命中 ──

async def test_precheck_cached_sufficient_passes():
    """缓存命中且余额充足 → 放行，不查库（不触发异步 session 读）。"""
    redis = FakeRedis(initial={BALANCE_KEY.format(tenant_id=1): "100.00"})
    session = FakeSession()  # 无任何行——若误查库会拿到 None → 402，故此处须 0 次访问
    svc = BalanceService(redis)

    await svc.precheck(session, tenant_id=1, cost=Decimal("10.00"))

    assert redis.sets == []  # 命中缓存不回写


async def test_precheck_cached_insufficient_raises_402():
    """缓存命中但余额 < 成本 → 402。"""
    redis = FakeRedis(initial={BALANCE_KEY.format(tenant_id=1): "5.00"})
    svc = BalanceService(redis)
    session = FakeSession()

    with pytest.raises(InsufficientBalanceError):
        await svc.precheck(session, tenant_id=1, cost=Decimal("10.00"))


# ── precheck：缓存未命中 → 查库回填 ──

async def test_precheck_miss_backfills_sufficient():
    """未命中 → 查库余额回填缓存（带 TTL）→ 充足放行。"""
    redis = FakeRedis()
    session = FakeSession([Balance(tenant_id=1, balance=Decimal("200.00"))])
    svc = BalanceService(redis)

    await svc.precheck(session, tenant_id=1, cost=Decimal("10.00"))

    assert redis.sets == [(BALANCE_KEY.format(tenant_id=1), "200.00", BALANCE_CACHE_TTL)]


async def test_precheck_miss_no_row_raises_402():
    """未命中且租户无 balance 行 → 回填 0 → 402。"""
    redis = FakeRedis()
    svc = BalanceService(redis)
    session = FakeSession()  # balances 表空

    with pytest.raises(InsufficientBalanceError):
        await svc.precheck(session, tenant_id=1, cost=Decimal("10.00"))

    assert redis.sets == [(BALANCE_KEY.format(tenant_id=1), "0", BALANCE_CACHE_TTL)]


def test_aclose_closes_redis():
    """aclose → 底层 redis 连接关闭（lifespan 退出调用）。"""
    redis = FakeRedis()
    service = BalanceService(redis)
    import asyncio

    asyncio.run(service.aclose())
    assert redis.closed is True