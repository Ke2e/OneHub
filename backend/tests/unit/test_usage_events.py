"""W3 任务 2 RED：用量事件发射（XADD Redis Stream）与消费幂等落库。

链路：网关转发完成后捕获 usage → XADD `usage:events` → worker 消费组读取
→ 幂等落 `usage_records`（request_id 全局唯一索引兜底，二插不新增）。

本测试覆盖（纯 Python + FakeRedis/FakeSession，不启真 Redis/Celery 进程）：
- 事件载荷构造（build_usage_event 纯函数，含幂等锚点 request_id）
- Producer.emit 把事件 XADD 进内存 Redis 流（记录调用/字段）
- Consumer.consume 读流 → 幂等插入 UsageRecord
- 重复 request_id 二消费仅落一行（幂等去重，对齐 usage_records.request_id unique）
"""

import json
import pytest

import app.services.usage_events as usage_events
from app.models import UsageRecord
from tests._fake_db import FakeSession


class FakeRedis:
    """内存流桩：xadd 追加到按流名分组的有序列表，供下游事件列迭代。"""

    def __init__(self):
        self.streams: dict[str, list[dict]] = {}

    async def xadd(self, name, fields, *, id_="*", maxlen=None, approximate=True):
        stream = self.streams.setdefault(name, [])
        entry = {"id": len(stream) + 1, "fields": fields}
        stream.append(entry)
        return entry["id"]

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


# ── 1) 事件载荷构造（纯函数） ──


def test_build_usage_event_serializes_usage_fields():
    """build_usage_event 纯函数：把转发结果映射到事件 dict，含幂等锚点与全部落库字段。"""
    ev = usage_events.build_usage_event(
        request_id="abc",
        api_key_id=3,
        channel_id=1,
        model="deepseek-v4-flash-0731",
        prompt_tokens=12,
        completion_tokens=5,
        latency_ms=230,
        status_code=200,
    )
    assert ev["request_id"] == "abc"
    assert ev["api_key_id"] == 3
    assert ev["channel_id"] == 1
    assert ev["model"] == "deepseek-v4-flash-0731"
    assert ev["prompt_tokens"] == 12
    assert ev["completion_tokens"] == 5
    assert ev["latency_ms"] == 230
    assert ev["status_code"] == 200


def test_build_usage_event_computes_total():
    """total_tokens 由 prompt+completion 计算得出（供展示/对账用）。"""
    ev = usage_events.build_usage_event(
        request_id="abc", api_key_id=1, model="m", prompt_tokens=7, completion_tokens=9
    )
    assert ev["total_tokens"] == 16


# ── 2) Producer.emit：XADD 进 Redis Stream ──


@pytest.mark.asyncio
async def test_producer_emit_xadds_event():
    """emit 把事件写入固定流名 usage:events，字段 JSON 语义可还原。"""
    redis = FakeRedis()
    producer = usage_events.UsageProducer(redis)
    ev = _valid_event()

    await producer.emit(ev)

    assert "usage:events" in redis.streams
    assert redis.streams["usage:events"][0]["fields"] == ev
    assert producer.stream_name == "usage:events"


# ── 3) Consumer.consume：读流 → 幂等落 UsageRecord ──


@pytest.mark.asyncio
async def test_consumer_consume_inserts_usage_record():
    """consume 读流内事件 → 幂等插入 UsageRecord（含 usage 字段与 cost 空缺）。"""
    redis = FakeRedis()
    producer = usage_events.UsageProducer(redis)
    session = FakeSession()
    await producer.emit(_valid_event())

    result = await usage_events.UsageConsumer(session, redis).consume()

    assert result == 1
    rows = session.stored(UsageRecord)
    assert len(rows) == 1
    row = rows[0]
    assert row.request_id == "11111111-1111-1111-1111-111111111111"
    assert row.api_key_id == 3
    assert row.channel_id == 1
    assert row.model == "deepseek-v4-flash-0731"
    assert row.prompt_tokens == 12
    assert row.completion_tokens == 5


@pytest.mark.asyncio
async def test_consumer_consume_is_idempotent_on_duplicate_request_id():
    """同 request_id 二消费：幂等去重，仅落一行（对齐 usage_records.request_id unique）。"""
    redis = FakeRedis()
    producer = usage_events.UsageProducer(redis)
    session = FakeSession()
    ev = _valid_event()
    await producer.emit(ev)
    await producer.emit(dict(ev))  # 同 request_id 重复事件（重放/重复消费场景）

    result = await usage_events.UsageConsumer(session, redis).consume()

    # 两事件都事件级进流，但落库按 request_id 幂等 → 仅 1 行
    assert result == 1
    assert len(session.stored(UsageRecord)) == 1