"""W2 任务 4 RED：幂等键服务层单测（Redis Lua 原子占位，保护清单手写）。

策略（对齐 test_rate_limit 风格）：
- Lua 脚本本体只在真机验证（redis EVAL + NX 原子语义），单测注入 fake eval /
  fake get/set，覆盖 Python 侧全部逻辑：三态判定分发、轮询接管、超时 409、
  complete 写缓存 + 删占位、cancel 释放占位、TTL 参数透传。
- 端点集成（gateway）另文件 test_gateway_idempotency.py，这里只测服务层。
"""

import asyncio

import pytest

from app.core.errors import OpenAIError
from app.services.idempotency import (
    IdempotencyOutcome,
    IdempotencyService,
)

VALID = "11111111-2222-3333-4444-555555555555"


class FakeRedis:
    """服务层离线桩：eval 结果可编程；get 从内存字典读（轮询场景边写边读）。"""

    def __init__(self, eval_result=None):
        self.eval_result = eval_result  # 常量值或 callable（每次 eval 求值）
        self.data: dict = {}
        self.calls: list[tuple] = []
        self.closed = False

    async def eval(self, script, numkeys, *args):
        self.calls.append(("eval", numkeys, args))
        r = self.eval_result() if callable(self.eval_result) else self.eval_result
        if r and r[0] == "claimed":
            # 对齐 Lua 语义：占位成功/判定的前提是占位键存在（args[1]=KEYS[2]）
            self.data.setdefault(args[1], args[2])
        return r

    async def get(self, key):
        self.calls.append(("get", key))
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        self.data[key] = value

    async def delete(self, key):
        self.calls.append(("delete", key))
        self.data.pop(key, None)

    async def aclose(self):
        self.closed = True


def _service(redis: FakeRedis, **kw) -> IdempotencyService:
    return IdempotencyService(redis, **kw)


def _payload() -> dict:
    """形如 provider.chat 返回的非流式响应体（缓存/回放对象）。"""
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


# ── 三态判定分发（Lua 返回值 → outcome） ──

def test_cached_returns_payload():
    """Lua 返 cached → (CACHED, 反序列化 payload)，直接回放不轮询。"""
    payload = _payload()
    redis = FakeRedis(eval_result=["cached", '{"key":"v"}'])
    svc = _service(redis)
    outcome, body = asyncio.run(svc.get_or_acquire(key_id=1, idem_key=VALID))
    assert outcome == IdempotencyOutcome.CACHED
    assert body == {"key": "v"}
    # KEYS[1]=缓存键  KEYS[2]=占位键，claim ARGV 为随机占位值 + 占位 TTL
    _, numkeys, args = redis.calls[0]
    assert numkeys == 2
    assert args[0] == f"idem:1:{VALID}"
    assert args[1] == f"idem:1:{VALID}:claim"
    assert len(args[2]) > 0
    assert args[3] == 30


def test_acquired_when_new():
    """Lua 返 new → (ACQUIRED, None)，调用方接管继续转发。"""
    redis = FakeRedis(eval_result=["new", ""])
    svc = _service(redis)
    outcome, body = asyncio.run(svc.get_or_acquire(key_id=1, idem_key=VALID))
    assert outcome == IdempotencyOutcome.ACQUIRED
    assert body is None


def test_claimed_polls_until_result_cached():
    """Lua 返 claim（并发同 key）→ 轮询缓存；得主写缓存后回放。"""
    redis = FakeRedis(eval_result=["claimed", "other-uuid"])
    svc = _service(redis, poll_interval=0.005)
    payload = _payload()

    async def _run():
        task = asyncio.create_task(svc.get_or_acquire(key_id=1, idem_key=VALID))
        await asyncio.sleep(0.01)
        redis.data[f"idem:1:{VALID}"] = '{"x":1}'  # 得主完成，写入缓存
        return await task

    outcome, body = asyncio.run(_run())
    assert outcome == IdempotencyOutcome.CACHED
    assert body == {"x": 1}
    # 至少轮询了一次缓存键
    gets = [c for c in redis.calls if c[0] == "get"]
    assert len(gets) >= 1


def test_claimed_takes_over_when_claim_dropped():
    """轮询中占位键消失（得主失败已撤销）→ 转 (ACQUIRED, None) 由当前请求接管。"""
    redis = FakeRedis(eval_result=["claimed", "other-uuid"])
    svc = _service(redis, poll_interval=0.005)
    base, claim = f"idem:1:{VALID}", f"idem:1:{VALID}:claim"

    async def _run():
        task = asyncio.create_task(svc.get_or_acquire(key_id=1, idem_key=VALID))
        await asyncio.sleep(0.01)
        redis.data.pop(claim, None)  # 得主声明失败并撤销占位
        return await task

    outcome, body = asyncio.run(_run())
    assert outcome == IdempotencyOutcome.ACQUIRED
    assert body is None


def test_claimed_timeout_raises_409():
    """轮询超时仍未出结果 → 409 conflict_error（得主在途太久，调用方应重试）。"""
    redis = FakeRedis(eval_result=["claimed", "other-uuid"])
    svc = _service(redis, wait_timeout=0.05, poll_interval=0.02)

    with pytest.raises(OpenAIError) as ei:
        asyncio.run(svc.get_or_acquire(key_id=1, idem_key=VALID))
    assert ei.value.status_code == 409
    assert ei.value.error_type == "conflict_error"


# ── 结果写回与占位释放 ──

def test_complete_sets_cache_with_ttl_and_deletes_claim():
    """得主转发完成：写缓存（EX=24h 窗口）+ 删除占位。"""
    import json

    redis = FakeRedis()
    svc = _service(redis)
    payload = _payload()
    asyncio.run(svc.complete(key_id=1, idem_key=VALID, payload=payload))
    ops = [c[0] for c in redis.calls]
    assert ops == ["set", "delete"]
    _, key, ex = redis.calls[0]
    assert key == f"idem:1:{VALID}"
    assert ex == 24 * 3600
    assert json.loads(redis.data[key]) == payload
    assert redis.calls[1] == ("delete", f"idem:1:{VALID}:claim")
    assert f"idem:1:{VALID}:claim" not in redis.data


def test_cancel_deletes_claim_only():
    """得主转发失败/被 429 挡回：只删占位（不写缓存），后续请求可重试。"""
    redis = FakeRedis()
    svc = _service(redis)
    redis.data[f"idem:1:{VALID}:claim"] = "mine"
    asyncio.run(svc.cancel(key_id=1, idem_key=VALID))
    assert redis.calls == [("delete", f"idem:1:{VALID}:claim")]
    assert f"idem:1:{VALID}:claim" not in redis.data


def test_aclose_closes_redis():
    """aclose → 底层 redis 连接关闭（lifespan 退出时调用）。"""
    redis = FakeRedis()
    svc = _service(redis)
    asyncio.run(svc.aclose())
    assert redis.closed is True