"""T020 流式 usage 端到端验证（US3 验收场景自动化）：Mock 上游录制 SSE 完整对话流。

链路全级联：DeepSeekProvider.chat_stream()（真实 httpx stream 协议层 + SSELineParser
+ UsageTracker）← MockTransport 返回录制 SSE 流。断言（SC-002）：

- 请求载荷已注入 stream_options.include_usage=true（T019 注入端就位才可能拿到 usage）
- 上游返回 usage → 末尾数据事件 usage 非零正整数，且只有末尾 chunk 带 usage
- 上游不返 usage → 末尾数据事件 usage 缺省 0（contracts 兜底），delta 透传不回退
"""

import json

import httpx

from app.providers.deepseek import DeepSeekProvider
from app.schemas.chat import ChatCompletionRequest, Message

REQ = ChatCompletionRequest(
    model="deepseek-v4-flash-0731",
    messages=[Message(role="user", content="hi")],
    stream=True,
)

# 录制流 A：上游注入 include_usage 生效——delta 两段 + 末尾 usage chunk + [DONE]
SSE_WITH_USAGE = (
    'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":100,"model":"m",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":100,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
    'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":100,"model":"m",'
    '"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}\n\n'
    "data: [DONE]\n\n"
)

# 录制流 B：渠道不支持 include_usage——只有 delta，无 usage chunk，[DONE] 正常收尾
SSE_WITHOUT_USAGE = (
    'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":100,"model":"m",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":100,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
    "data: [DONE]\n\n"
)


def _provider(remote_body: bytes) -> tuple[DeepSeekProvider, dict]:
    """注入 MockTransport 的 provider：截获传出载荷 + 回放录制 SSE 流。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(remote_body),
        )

    p = DeepSeekProvider(api_key="test-key")
    p._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=p._headers(),
        base_url=p.base_url,
    )
    return p, captured


async def _events(p: DeepSeekProvider) -> list[dict]:
    try:
        return [e async for e in p.chat_stream(REQ)]
    finally:
        await p.aclose()  # 释放连接池，避免 pending task 泄漏


async def test_include_usage_injected_when_streaming():
    """T019 注入端：stream=true 载荷带 stream_options.include_usage=true（零配置前提）。"""
    p, captured = _provider(SSE_WITH_USAGE.encode())
    await _events(p)
    assert captured["payload"]["stream_options"] == {"include_usage": True}


async def test_upstream_usage_arrives_in_last_chunk():
    """SC-002：末尾 chunk usage 为非零正整数；只有末尾 chunk 携带 usage。"""
    p, _ = _provider(SSE_WITH_USAGE.encode())
    events = await _events(p)

    tail = events[-1]
    assert tail["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    assert tail["usage"]["prompt_tokens"] > 0  # 非零（SC-002 判定标准）
    assert tail["usage"]["completion_tokens"] > 0
    # 中间 chunk 不带 usage（OpenAI 语义：usage 仅在最后一个数据 chunk）
    for e in events[:-1]:
        assert "usage" not in e
    # delta 累计完整（usage 提取不破坏 US2 透传）
    content = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in events
        if e.get("choices")
    )
    assert content == "你好"


async def test_missing_upstream_usage_defaults_to_zero():
    """上游未返 usage → 末尾兜底事件 usage=0，不报错，delta 原样透传（contracts）。"""
    p, _ = _provider(SSE_WITHOUT_USAGE.encode())
    events = await _events(p)

    assert len(events) == 3  # delta 2 个（role 块 + content 块）+ 合成 usage 兜底 1 个
    tail = events[-1]
    assert tail["object"] == "chat.completion.chunk"
    assert tail["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert tail["choices"] == []
    # 兜底事件前 delta 仍在原位透传（role 块无 content → None）
    assert [e["choices"][0]["delta"].get("content") for e in events[:-1]] == [None, "你好"]