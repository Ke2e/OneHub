"""T011 BaseProvider：渠道转发模板方法基类（research.md D1 候选 B 定稿）。

通用 OpenAI 兼容转发逻辑单点实现于此：httpx.AsyncClient 连接池复用、
请求载荷组装、上游错误 → OpenAI 结构映射、响应解析。子类只填差异点
（_headers + 构造参数）。

接口（research D1）：
    _headers()     抽象点：渠道鉴权头
    chat()          非流式：完整请求 → ChatCompletionResponse（US1 MVP）
    chat_stream()   流式：AsyncIterator 逐 chunk（Phase 4 T016/T017，保护清单 TDD）
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from app.core.errors import (
    AuthenticationError,
    RateLimitError,
    UpstreamError,
)
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.forward import sse_events, with_usage

# 上游请求默认超时：connect 隔离握手，read 兜底慢响应（SC-004「不挂死等待」）
_DEFAULT_TIMEOUT = httpx.Timeout(timeout=60.0, connect=10.0)


class BaseProvider(ABC):
    """模板方法：请求组装/错误映射/响应解析已实现，子类补 _headers。"""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """OpenAI 兼容 base_url 规整：统一补 /v1 前缀。

        全网关转发统一走 {base_url}/chat/completions；官方 DeepSeek
        （https://api.deepseek.com 两种路径均支持）与中转平台（仅 /v1）都兼容、
        已含 /v1 的地址（https://x/v1）不重复拼接。
        """
        url = base_url.rstrip("/")
        return url if url.endswith("/v1") else f"{url}/v1"

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建 AsyncClient：连接池连通保活、跨请求复用（面试点：TCP 复用省握手）。

        headers 一次性注入（基类构造时 _headers() 已绑定），关闭时 aclose() 释放。
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=_DEFAULT_TIMEOUT,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def aclose(self) -> None:
        """释放连接池；app 关闭时由 lifespan 调用（见 main.py）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    def _headers(self) -> dict[str, str]:
        """渠道鉴权头（差异点）：DeepSeek = Bearer 密钥。"""

    # ---- 载荷组装（模板方法内部件，公开便于测试/复用） ----

    def _build_payload(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """请求体 → 上游载荷：exclude_none 丢弃未填参数。

        stream=true 时注入 stream_options.include_usage（合同 Q2: A 决议：
        调用方零配置拿 usage；非流式不注入）。其余未建模字段经 extra 已
        在 model_dump 中原样保留（透传）。
        """
        payload = request.model_dump(exclude_none=True)
        if request.stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ---- 上游错误映射（contracts 错误节：状态码语义保留 + OpenAI 结构零堆栈） ----

    def _map_upstream_error(self, status_code: int) -> UpstreamError | RateLimitError | AuthenticationError:
        """非 2xx 状态码 → OpenAI 结构异常。

        429 语义保留（rate_limit_error）；401 = 渠道密钥问题（authentication_error，
        文案不含上游明文防泄漏，D4）；其余上游 5xx 一律 502 api_error。
        """
        if status_code == 429:
            return RateLimitError()
        if status_code == 401:
            return AuthenticationError(message="upstream authentication failed")
        return UpstreamError(message="upstream server error", status_code=502)

    def _handle_response(self, resp: httpx.Response) -> ChatCompletionResponse:
        """2xx 解析：校验结构合法性，异常/格式错误 → 502 api_error 防堆栈。"""
        if resp.is_error:
            raise self._map_upstream_error(resp.status_code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise UpstreamError(
                message="upstream returned invalid response", status_code=502
            ) from exc
        if not isinstance(data, dict) or "choices" not in data:
            raise UpstreamError(
                message="unexpected upstream response format", status_code=502
            ) from None
        return ChatCompletionResponse.model_validate(data)

    # ---- 通道对外接口 ----

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """非流式：POST /chat/completions → OpenAI 兼容响应（US1 MVP 主路径）。"""
        try:
            resp = await self.client.post("/chat/completions", json=self._build_payload(request))
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                message="upstream request timed out", status_code=504
            ) from exc
        except httpx.ConnectError as exc:
            raise UpstreamError(
                message="upstream service unavailable", status_code=502
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                message="upstream request failed", status_code=502
            ) from exc
        return self._handle_response(resp)

    async def chat_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[dict[str, Any]]:
        """流式：httpx stream() + aiter_lines() → SSE 解析器逐事件产出（US2 主路径）。

        链路（T016+T019）：POST /chat/completions（stream=true 载荷已注入 include_usage）
        → 状态码检查（非 2xx 复用 _map_upstream_error 错误映射）
        → sse_events() 增量产出事件 dict → with_usage() 透传 + 流尾 usage 兜底
        （上游已返 usage 原样透传；未返合成 usage=0 事件。调用方零配置总有末尾 usage）。
        [DONE]/上游中断均自然收敛，不挂死调用方。
        调用方断连：async with 退出关闭上游连接，终止上游消费（T016「断开检测终止上游」）。
        """
        payload = self._build_payload(request)
        try:
            async with self.client.stream(
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.is_error:
                    raise self._map_upstream_error(resp.status_code)
                async for chunk in with_usage(sse_events(resp.aiter_lines())):
                    yield chunk
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                message="upstream request timed out", status_code=504
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                message="upstream request failed", status_code=502
            ) from exc