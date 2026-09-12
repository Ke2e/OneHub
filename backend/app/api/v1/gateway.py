"""T013/T017 网关面端点：POST /v1/chat/completions（US1 非流式 + US2 流式 SSE 透传）。

链路：鉴权依赖 → model ∈ models 表 enabled 集合校验（缺 → ModelNotFoundError）
→ 渠道实例化（W1 单渠道：app.state 单例 DeepSeekProvider，连接池进程级复用）
→ stream=false 走 provider.chat() 非流式；stream=true 走 provider.chat_stream()
  逐事件转 SSE 行（data: {chunk}\n\n，[DONE] 收尾）直通调用方（contracts 流式节）。
"""

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ModelNotFoundError
from app.core.security import require_gateway_api_key
from app.models import ApiKey, Model
from app.providers.deepseek import DeepSeekProvider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.keys import check_model_whitelist

router = APIRouter(prefix="/v1")


async def _stream_events(
    provider: DeepSeekProvider, req: ChatCompletionRequest
) -> AsyncIterator[str]:
    """流式透传：上游事件 dict → SSE 行（data: {json}\n\n），[DONE] 正常收尾。

    坏 JSON 已在解析器层跳过；调用方断连时本生成器被取消，provider 的 async with
    退出并关闭上游连接（T016「断开检测终止上游」，无额外代码）。
    """
    async for chunk in provider.chat_stream(req):
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


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
) -> ChatCompletionResponse | StreamingResponse:
    """对话端点：非流式返回完整 Chat Completion；流式返回 text/event-stream 逐块直通。"""

    # 1) model 可用性校验（W1 数据源 = models 表 enabled 集合）
    async with AsyncSession(request.app.state.engine) as session:
        model = await session.scalar(
            select(Model).where(
                Model.model_name == req.model,
                Model.enabled.is_(True),
            )
        )
    if model is None:
        raise ModelNotFoundError()

    # 2) key 级模型白名单（W2 任务 2）：白名单非空且不含请求 model → 404（不泄露 key 授权粒度）
    if not check_model_whitelist(api_key, req.model):
        raise ModelNotFoundError()

    # 3) 渠道实例化：app.state 单例（lifespan 构造，连接池复用，关闭时 aclose）
    provider: DeepSeekProvider = request.app.state.deepseek_provider

    # 4) 透传转发：上游错误映射在 provider 内部完成（T011），统一 OpenAI 错误出口兜底
    if req.stream:
        return StreamingResponse(
            _stream_events(provider, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁用反向代理缓冲，保证逐块即时下发
            },
        )
    return await provider.chat(req)