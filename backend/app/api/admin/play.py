"""管理面 Playground 代理转发（W4 任务 2，Asize 拍板：代理转发 + JWT）。

POST /api/play/chat：
- require_admin（管理面 JWT）鉴权，与网关面 sk-key 鉴权解耦
- 请求体复用 OpenAI ChatCompletionRequest（流式 stream 可选）
- 转发链路复用网关智能路由：候选收集（同 model 多行 enabled 挂到的 channel）
  → 熔断过滤 + 加权轮询 + 指数退避重试（app.state.smart_router）
  → provider 按渠道惰性复用（app.state.channel_providers）
- 流式 response 以 text/event-stream 透传；非流式返回 OpenAI 结构
- 仅换鉴权边界：不叠加面向外部调用方的限流/幂等/余额预检（调试语义）

provider 构造复用 gateway._provider_for_channel（连接池惰性缓存 + lifespan 统一释放）。
"""

from typing import AsyncIterator

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.gateway import _provider_for_channel
from app.core.errors import ModelNotFoundError, UpstreamError
from app.core.security import require_admin
from app.models import Channel, Model
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.router import Candidate
from app.services.smart_router import SmartRouter

router = APIRouter(prefix="/api/play", tags=["admin"])


def get_smart_router(request: Request) -> SmartRouter:
    """智能路由单例依赖：lifespan 构造（W4 任务 1）。"""
    return request.app.state.smart_router


async def _collect_candidates(request: Request, chat: ChatCompletionRequest):
    """收集转发候选：同 model 多行 enabled 挂到的渠道（null 则回退单渠道）。"""
    async with AsyncSession(request.app.state.engine) as session:
        model_rows = (
            await session.scalars(
                select(Model).where(
                    Model.model_name == chat.model, Model.enabled.is_(True)
                )
            )
        ).all()
    if not model_rows:
        raise ModelNotFoundError()
    model = model_rows[0]
    wanted_ids = {m.channel_id for m in model_rows if m.channel_id}
    if not wanted_ids:
        return model, [], {}
    async with AsyncSession(request.app.state.engine) as session:
        channel_map = {c.id: c for c in (await session.scalars(select(Channel))).all() if c.id in wanted_ids}
    candidates = [
        Candidate(
            channel_id=m.channel_id,
            weight=channel_map[m.channel_id].weight,
            available=(channel_map[m.channel_id].status == "healthy"),
        )
        for m in model_rows
        if m.channel_id in channel_map
    ]
    return model, candidates, channel_map


async def _stream_proxy(provider, chat: ChatCompletionRequest) -> AsyncIterator[str]:
    """流式透传：chunk dict → SSE 行（data: {json}\n\n）+ [DONE] 收尾。"""
    async for chunk in provider.chat_stream(chat):
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat", response_model=ChatCompletionResponse)
async def play_chat(
    request: Request,
    chat: ChatCompletionRequest,
    _claims: dict = Depends(require_admin),
    smart_router: SmartRouter = Depends(get_smart_router),
):
    """Playground 对话：JWT 鉴权 → 智能路由转发到上游（流式/非流式）。"""
    model, candidates, channel_map = await _collect_candidates(request, chat)

    if chat.stream:
        if candidates:
            chosen = await smart_router.pick_channel(candidates)
            if chosen is None:
                raise UpstreamError(
                    message="all channels unavailable", status_code=503
                )
            provider = _provider_for_channel(request, channel_map[chosen.channel_id])
        else:
            provider = request.app.state.deepseek_provider
        return StreamingResponse(
            _stream_proxy(provider, chat),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    if candidates:
        async def _upstream(cid: int):
            return await _provider_for_channel(request, channel_map[cid]).chat(chat)

        response = await smart_router.forward(candidates, _upstream)
    else:
        response = await request.app.state.deepseek_provider.chat(chat)
    return response