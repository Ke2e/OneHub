"""T018 流式 usage 提取单测（保护清单，测试先行 RED → T019 实现转 GREEN）。

覆盖（tasks.md T018 要求三项 + 透传语义守卫）：
- 末尾 chunk usage 提取：UsageTracker 从事件流中提取 usage（12/5/17 样例）
- delta 累计口径：提取不破坏逐块 delta 透传，累计拼接完整（US2 语义回归）
- 上游未返回 usage → 缺省 0 不报错：正常 [DONE] 结束无 usage → 末尾合成 0 事件
- EOF 截断无 usage → 同样缺省 0 自然收敛（contracts「上游异常未返 usage」兜底）
- with_usage() 异步适配：合成 usage 事件作为最后一个产出（gateway 的 [DONE] 前）
"""

from app.services.forward import UsageTracker, with_usage

DELTA_EVENTS = [
    {
        "id": "cmpl-u",
        "object": "chat.completion.chunk",
        "created": 123,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}],
    },
    {
        "id": "cmpl-u",
        "object": "chat.completion.chunk",
        "created": 123,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": "，世界"}, "finish_reason": None}],
    },
]

USAGE_EVENT = {
    "id": "cmpl-u",
    "object": "chat.completion.chunk",
    "created": 123,
    "model": "m",
    "choices": [],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
}


def test_usage_extracted_from_last_chunk():
    """末尾 chunk 携带 usage → 提取出 token 数，且事件原样透传（usage chunk 本身也透传）。"""
    tracker = UsageTracker()
    for event in DELTA_EVENTS + [USAGE_EVENT]:
        assert tracker.feed(event) == [event]  # 透传语义：逐事件原样返回，不拦截
    assert tracker.usage == USAGE_EVENT["usage"]
    assert tracker.finish() == []  # 已提取到 usage，不再合成兜底事件


def test_delta_accumulation_untouched():
    """delta 累计口径：usage 提取不影响逐块 delta 透传，累计拼接完整。"""
    tracker = UsageTracker()
    out: list[dict] = []
    for event in DELTA_EVENTS:
        out.extend(tracker.feed(event))
    out.extend(tracker.finish())
    # 仅累计带 delta 的 chunk（合成 usage 兜底事件 choices 为空，OpenAI 语义）
    content = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in out
        if e.get("choices")
    )
    assert content == "你好，世界"


def test_missing_usage_defaults_to_zero_without_error():
    """上游未返 usage（include_usage 未生效/渠道不支持）→ 缺省 0，不报错。"""
    tracker = UsageTracker()
    out: list[dict] = []
    for event in DELTA_EVENTS:
        out.extend(tracker.feed(event))
    assert tracker.usage is None  # 提取不到 = 视为缺省
    out.extend(tracker.finish())
    assert len(out) == len(DELTA_EVENTS) + 1  # 末尾多出一个合成兜底事件
    tail = out[-1]
    assert tail["object"] == "chat.completion.chunk"
    assert tail["choices"] == []  # OpenAI usage chunk 语义：无 delta
    assert tail["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_truncated_stream_without_usage_converges():
    """EOF 截断且无 usage → 合成 0 兜底，不抛异常（contracts「上游中断」分支）。"""
    tracker = UsageTracker()
    out: list[dict] = []
    for event in DELTA_EVENTS:
        out.extend(tracker.feed(event))
    out.extend(tracker.finish())  # 模拟 EOF：无 [DONE] 也自然收敛
    assert out[-1]["usage"]["total_tokens"] == 0


async def test_with_usage_appends_zero_event_as_last_output():
    """异步适配：合成 usage 事件是流的最后一个产出（gateway 紧随后写 [DONE]）。"""

    async def _gen():
        for e in DELTA_EVENTS:
            yield e

    got = [e async for e in with_usage(_gen())]
    assert len(got) == len(DELTA_EVENTS) + 1
    assert got[-1]["usage"]["total_tokens"] == 0
    # 兜底事件之前的所有 delta 仍在原位透传
    assert [e["choices"][0]["delta"]["content"] for e in got[:-1]] == ["你好", "，世界"]