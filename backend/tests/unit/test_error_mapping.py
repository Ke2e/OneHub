"""T014 错误映射单测（SC-004 验收证据）：上游五类错误 → OpenAI 协议结构。

覆盖：401 / 429 / 502 / 超时 / 连接拒绝 五类异常，断言异常类型、HTTP 状态码、
OpenAI error type 与 message 防泄漏语义（不透露上游明文）。保护清单外，但为
D4 错误映射的回归网（T011 实现）。
"""

import httpx
import pytest

from app.core.errors import (
    AuthenticationError,
    OpenAIError,
    RateLimitError,
    UpstreamError,
)
from app.providers.deepseek import DeepSeekProvider
from app.schemas.chat import ChatCompletionRequest, Message

REQ = ChatCompletionRequest(
    model="deepseek-chat", messages=[Message(role="user", content="hi")]
)


def _provider(handler) -> DeepSeekProvider:
    """注入 MockTransport 的 provider：上传真实转发链路、替换传输层为桩。"""
    p = DeepSeekProvider(api_key="test-key")
    p._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=p._headers(),
        base_url=p.base_url,
    )
    return p


def _assert_openai_error(
    exc: OpenAIError, status_code: int, error_type: str, extra: str = ""
) -> None:
    """断言异常携带 OpenAI 结构字段（message/type/param/code 的契约来源）。"""
    assert exc.status_code == status_code
    assert exc.error_type == error_type
    # param/code 缺省为 None，结构键由 errors.py _body 统一补全（此处不重复断言）
    assert isinstance(exc.message, str) and exc.message
    if extra:  # D4：message 通用文案，防上游信息泄漏（extra 非空才断言）
        assert extra not in exc.message


async def test_upstream_401_maps_authentication_error():
    """上游 401（渠道密钥鉴权失败）→ 401 authentication_error，不含上游明文。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(AuthenticationError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 401, "authentication_error", "invalid api key")


async def test_upstream_429_maps_rate_limit_error():
    """上游 429（限流/超载）→ 429 rate_limit_error，状态码语义保留（contracts）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "too many requests"}})

    with pytest.raises(RateLimitError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 429, "rate_limit_error", "too many requests")


async def test_upstream_502_maps_api_error():
    """上游 502（网关错误/不可用）→ 502 api_error。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": {"message": "bad gateway"}})

    with pytest.raises(UpstreamError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 502, "api_error", "bad gateway")


async def test_upstream_timeout_maps_504():
    """上游读超时 → 504 api_error（契约：超时 502/504 且不挂死等待）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(UpstreamError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 504, "api_error", "read timed out")


async def test_upstream_connection_refused_maps_502():
    """上游连接拒绝 → 502 api_error（上游不可用）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(UpstreamError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 502, "api_error", "connection refused")


async def test_upstream_200_invalid_structure_maps_502():
    """上游 200 但响应体非法（非 OpenAI 结构）→ 502 api_error，防意外透传。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(UpstreamError) as ei:
        await _provider(handler).chat(REQ)
    _assert_openai_error(ei.value, 502, "api_error")


async def test_upstream_success_passthrough():
    """回归网对照：2xx 合法结构正常返回，不误映射（确保映射只对错误生效）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    resp = await _provider(handler).chat(REQ)
    assert resp.choices[0].message.content == "ok"
    assert resp.usage is not None and resp.usage.total_tokens == 2