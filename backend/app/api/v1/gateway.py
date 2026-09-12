"""T013 网关面端点：POST /v1/chat/completions（US1 非流式对话 MVP）。

链路：鉴权依赖 → model ∈ models 表 enabled 集合校验（缺 → ModelNotFoundError）
→ 渠道实例化（W1 单渠道：app.state 单例 DeepSeekProvider，连接池进程级复用）
→ provider.chat() 透传响应（含 usage）。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ModelNotFoundError
from app.core.security import require_gateway_api_key
from app.models import Model
from app.providers.deepseek import DeepSeekProvider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse

router = APIRouter(prefix="/v1")


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: Request,
    req: ChatCompletionRequest,
    _api_key: str = Depends(require_gateway_api_key),
) -> ChatCompletionResponse:
    """非流式对话：校验 + 转发，返回 OpenAI Chat Completion 结构（contracts 非流式节）。"""

    # 1) model 可用性校验（W1 数据源 = models 表 enabled 集合，种子里有 deepseek-chat/reasoner）
    async with AsyncSession(request.app.state.engine) as session:
        model = await session.scalar(
            select(Model).where(
                Model.model_name == req.model,
                Model.enabled.is_(True),
            )
        )
    if model is None:
        raise ModelNotFoundError()

    # 2) 渠道实例化：app.state 单例（lifespan 构造，连接池复用，关闭时 aclose）
    provider: DeepSeekProvider = request.app.state.deepseek_provider

    # 3) 透传转发：上游错误映射在 provider 内部完成（T011），统一 OpenAI 错误出口兜底
    return await provider.chat(req)