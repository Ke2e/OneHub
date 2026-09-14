"""T013/T017 网关面端点：POST /v1/chat/completions（US1 非流式 + US2 流式 SSE 透传）。

链路：鉴权依赖 → model ∈ models 表 enabled 集合校验（缺 → ModelNotFoundError）
→ 渠道实例化（W1 单渠道：app.state 单例 DeepSeekProvider，连接池进程级复用）
→ stream=false 走 provider.chat() 非流式；stream=true 走 provider.chat_stream()
  逐事件转 SSE 行（data: {chunk}\n\n，[DONE] 收尾）直通调用方（contracts 流式节）。
"""

import json
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ModelNotFoundError, RateLimitError, UpstreamError
from app.core.security import require_gateway_api_key
from app.models import ApiKey, Channel, Model
from app.providers.deepseek import DeepSeekProvider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.idempotency import IdempotencyOutcome, IdempotencyService
from app.services.billing import BalanceService, estimate_cost
from app.services.keys import check_model_whitelist
from app.services.rate_limit import RateLimiter, estimate_tokens
from app.services.router import Candidate
from app.services.smart_router import SmartRouter
from app.services.usage_events import UsageProducer, build_usage_event

router = APIRouter(prefix="/v1")


def get_rate_limiter(request: Request) -> RateLimiter:
    """限流单例依赖：lifespan 构造（持有 Redis 连接），端点经 Depends 注入。"""
    return request.app.state.rate_limiter


def get_idempotency(request: Request) -> IdempotencyService:
    """幂等键单例依赖：lifespan 构造（与限流共 Redis 连接，W2 任务 4）。"""
    return request.app.state.idempotency


def get_billing(request: Request) -> BalanceService:
    """余额预检单例依赖：lifespan 构造（W3 任务 1，Redis 余额缓存）。"""
    return request.app.state.billing


def get_usage_producer(request: Request) -> UsageProducer:
    """用量生产者单例依赖：lifespan 构造（W3 任务 2，XADD usage:events）。"""
    return request.app.state.usage_producer


def get_smart_router(request: Request) -> SmartRouter:
    """智能路由单例依赖：lifespan 构造（W4 任务 1，熔断过滤+加权轮询+退避重试）。"""
    return request.app.state.smart_router


def _provider_for_channel(request: Request, channel: Channel) -> DeepSeekProvider:
    """渠道 → provider 惰性复用：首次按 base_url/密钥构造（连接池），后续复用。

    app.state.channel_providers 字典缓存（lifespan 初始化、退出统一 aclose）。
    仅支持 provider=deepseek（当前唯一提供方，基类转发逻辑复用）；密钥沿用
    渠道表明文占位（W4 计费加密留待 ADR，本任务不改 DDL）。
    """
    table: dict[int, DeepSeekProvider] = request.app.state.channel_providers
    prov = table.get(channel.id)
    if prov is None:
        prov = DeepSeekProvider(
            api_key=channel.api_key_encrypted, base_url=channel.base_url
        )
        table[channel.id] = prov
    return prov


async def _stream_events(
    limiter: RateLimiter,
    provider: DeepSeekProvider,
    req: ChatCompletionRequest,
    usage_producer: UsageProducer | None,
    api_key: ApiKey,
    channel_id: int | None,
    request_id: str,
    started_at: float,
) -> AsyncIterator[str]:
    """流式透传：上游事件 dict → SSE 行（data: {json}\n\n），[DONE] 正常收尾。

    坏 JSON 已在解析器层跳过；调用方断连时本生成器被取消，provider 的 async with
    退出并关闭上游连接（T016「断开检测终止上游」，无额外代码）。finally 释放限流
    并发槽——流式期间槽位由本生成器持有，响应发完或断连都不泄漏。
    usage 在流尾 chunk 携带（with_usage 保证末尾必有）：透传完成后 XADD 用量事件，
    交 worker 异步幂等落库（W3 任务 2）。emit 失败不影响透传（计费尽力而为）。
    """
    try:
        usage: dict | None = None
        async for chunk in provider.chat_stream(req):
            if isinstance(chunk, dict) and chunk.get("usage"):
                usage = chunk["usage"]
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        if usage and usage_producer is not None:
            await usage_producer.emit(
                build_usage_event(
                    request_id=request_id,
                    api_key_id=api_key.id,
                    channel_id=channel_id,
                    model=req.model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    status_code=200,
                )
            )
    finally:
        limiter.release()


@router.get("/models")
async def list_models(
    request: Request,
    _api_key: ApiKey = Depends(require_gateway_api_key),
) -> dict:
    """模型列表（T021/US4）：models 表 enabled 集合 → OpenAI list 格式。

    契约 GET /v1/models 节：{object: "list", data: [{id, object: "model",
    created, owned_by}]}。表无 created/owned_by 列 → 协议壳层静态默认值
    （created=0 / owned_by="onehub"）；DB 层已按 enabled=true 过滤。
    """
    async with AsyncSession(request.app.state.engine) as session:
        rows = (
            await session.scalars(select(Model).where(Model.enabled.is_(True)))
        ).all()
    return {
        "object": "list",
        "data": [
            {
                "id": m.model_name,
                "object": "model",
                "created": 0,
                "owned_by": "onehub",
            }
            for m in rows
        ],
    }


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: Request,
    req: ChatCompletionRequest,
    api_key: ApiKey = Depends(require_gateway_api_key),
    limiter: RateLimiter = Depends(get_rate_limiter),
    idempotency: IdempotencyService = Depends(get_idempotency),
    billing: BalanceService = Depends(get_billing),
    usage_producer: UsageProducer = Depends(get_usage_producer),
    smart_router: SmartRouter = Depends(get_smart_router),
) -> ChatCompletionResponse | StreamingResponse:
    """对话端点：非流式返回完整 Chat Completion；流式返回 text/event-stream 逐块直通。"""

    # W3 任务 2：每次真实转发分配用量事件幂等锚点（usage_records.request_id unique），
    # 计时起点用于 latency 上报。转发生效后才 emit；缓存回放/失败不产生新事件。
    request_id = str(uuid.uuid4())
    started_at = time.monotonic()

    # 1) model 可用性校验（W1 数据源 = models 表 enabled 集合）。
    #    W4 同 model_name 可多行 → 之后作为多渠道候选；本处取首行做存在性/定价。
    async with AsyncSession(request.app.state.engine) as session:
        model_rows = (
            await session.scalars(
                select(Model).where(
                    Model.model_name == req.model,
                    Model.enabled.is_(True),
                )
            )
        ).all()
    if not model_rows:
        raise ModelNotFoundError()
    model = model_rows[0]

    # 2) key 级模型白名单（W2 任务 2）：白名单非空且不含请求 model → 404（不泄露 key 授权粒度）
    if not check_model_whitelist(api_key, req.model):
        raise ModelNotFoundError()

    # 3) 限流（W2 任务 3）：RPM/TPM 双维度令牌桶（Redis Lua）→ 超限 429 + Retry-After；
    #    并发槽（Semaphore）占满同 429。廉价检查放转发前，不扣 SLO 也不泄漏转发成本。
    allowed, retry_after = await limiter.check(
        api_key.id, api_key.rpm_limit, api_key.tpm_limit, estimate_tokens(req.messages)
    )
    if not allowed:
        raise RateLimitError(retry_after=retry_after or 1)

    # 4) 幂等键（W2 任务 4，限流后、转发前）：仅非流式生效（流式 SSE 不缓存）。
    #    命中缓存直接回放首次响应（不转发、不占并发槽）；占位成功继续；
    #    之后任何失败路径都要 cancel 释放占位，否则后续请求会一直等 409。
    idem_key = request.headers.get("Idempotency-Key") if not req.stream else None
    if idem_key:
        outcome, cached_body = await idempotency.get_or_acquire(api_key.id, idem_key)
        if outcome == IdempotencyOutcome.CACHED:
            return cached_body

    # 3.5) 余额预检（W3 任务 1，幂等回放后、并发槽前）：缓存命中不查库，不足 402。
    #     缓存回放的请求已计费，不必重复预检；放并发放行前做最廉检查，避免无偿占槽。
    cost = estimate_cost(
        model.input_price, model.output_price, estimate_tokens(req.messages)
    )
    async with AsyncSession(request.app.state.engine) as session:
        await billing.precheck(session, api_key.tenant_id, cost)

    if not limiter.try_acquire():
        if idem_key:
            await idempotency.cancel(api_key.id, idem_key)  # 被 429 挡回，释放占位可重试
        raise RateLimitError(retry_after=1)

    # 5) 渠道候选（W4 任务 1）：同一 model 的所有 enabled 行挂到的渠道 → 候选
    #    （权重/可用性取自 channel.status）。选中的渠道经 _provider_for_channel 惰性构造 provider。
    #    有候选走智能路由（熔断过滤 + 加权轮询 + 指数退避重试）；无候选（单渠道旧语义/
    #    测试桩 channel_id=None）回退 app.state 单例，保持既有行为不变。
    provider: DeepSeekProvider = request.app.state.deepseek_provider
    channel_map: dict[int, Channel] = {}
    candidates: list[Candidate] = []
    wanted_ids = {m.channel_id for m in model_rows if m.channel_id}
    if wanted_ids:
        async with AsyncSession(request.app.state.engine) as session:
            for ch in (await session.scalars(select(Channel))).all():
                if ch.id in wanted_ids:
                    channel_map[ch.id] = ch
        candidates = [
            Candidate(
                channel_id=m.channel_id,
                weight=channel_map[m.channel_id].weight,
                available=(channel_map[m.channel_id].status == "healthy"),
            )
            for m in model_rows
            if m.channel_id in channel_map
        ]

    # 6) 透传转发：上游错误映射在 provider 内部完成（T011），统一 OpenAI 错误出口兜底。
    #    流式仅"首个事件产出前"可重试，故流式走 pick_channel 单次选择（选到即透传，不重试）；
    #    非流式走 forward 带退避重试，全部候选失败抛 UpstreamError(503)。
    if req.stream:
        if candidates:
            chosen = await smart_router.pick_channel(candidates)
            if chosen is None:
                raise UpstreamError(
                    message="all channels unavailable", status_code=503
                )
            stream_provider = _provider_for_channel(
                request, channel_map[chosen.channel_id]
            )
            stream_channel_id = chosen.channel_id
        else:
            stream_provider = provider
            stream_channel_id = model.channel_id
        return StreamingResponse(
            _stream_events(
                limiter,
                stream_provider,
                req,
                usage_producer,
                api_key,
                stream_channel_id,
                request_id,
                started_at,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁用反向代理缓冲，保证逐块即时下发
            },
        )

    # 非流式：路由转发（有候选走智能路由；无候选回退单渠道）
    used_channel_id = model.channel_id
    try:
        if candidates:
            async def _upstream(cid: int):
                nonlocal used_channel_id
                used_channel_id = cid
                return await _provider_for_channel(
                    request, channel_map[cid]
                ).chat(req)

            response = await smart_router.forward(candidates, _upstream)
        else:
            response = await provider.chat(req)
    except BaseException:
        if idem_key:
            await idempotency.cancel(api_key.id, idem_key)  # 转发失败，撤销占位可重试
        raise
    finally:
        limiter.release()  # 非流式同步完成，槽位即释
    if idem_key:
        await idempotency.complete(api_key.id, idem_key, response)  # 成功 → 写缓存
    # 非流式：转发成功 → 发用量事件（usage 在响应对象；上游未返则不产生事件）
    if response.usage:
        await usage_producer.emit(
            build_usage_event(
                request_id=request_id,
                api_key_id=api_key.id,
                channel_id=used_channel_id,
                model=req.model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                status_code=200,
            )
        )
    return response