"""OneHub 应用入口：应用工厂 + lifespan 管理 SQLAlchemy async 引擎生命周期。"""

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.api.admin import router as admin_router
from app.api.v1.gateway import router as gateway_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.providers.deepseek import DeepSeekProvider
from app.services.idempotency import IdempotencyService
from app.services.rate_limit import RateLimiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时创建 async 引擎、渠道 provider 与限流单例，关闭时释放。

    create_async_engine 是惰性连接，应用可在 pg 未就绪时启动；
    实际查询时才建连（池预检 pool_pre_ping 应对连接回收）。
    provider 单例持有 httpx 连接池（进程级复用），关闭时 aclose() 释放。
    Redis.from_url 同样惰性：限流（W2 任务 3）首次 EVAL 才建连；aclose() 关闭。
    """
    settings = get_settings()
    engine: AsyncEngine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    app.state.engine = engine
    # T011/T013：单渠道 DeepSeek provider——密钥与 base_url 均来自 env（config 双键名），不入库
    app.state.deepseek_provider = DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    # W2 任务 3：令牌桶限流单例（Redis Lua EVAL + Semaphore 并发槽）
    app.state.rate_limiter = RateLimiter(
        aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    )
    # W2 任务 4：幂等键单例（Redis Lua 原子占位 + 响应缓存），独立连接与限流解耦
    app.state.idempotency = IdempotencyService(
        aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    )
    try:
        yield
    finally:
        await app.state.rate_limiter.aclose()
        await app.state.idempotency.aclose()
        await app.state.deepseek_provider.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    """应用工厂：网关路由注册 + 统一错误出口（Phase 2/3 接入）。"""
    app = FastAPI(
        title="OneHub Gateway",
        description="OpenAI 协议兼容的多模型 LLM API 聚合网关",
        version="0.1.0",
        docs_url="/docs",
        lifespan=lifespan,
    )
    # T013：OpenAI 兼容网关面端点（POST /v1/chat/completions，US1 MVP）
    app.include_router(gateway_router)
    # W2 任务 1：管理面端点（/api/auth + /api/keys，JWT 鉴权）
    app.include_router(admin_router)
    # T008：统一错误出口——所有非 2xx 重塑为 OpenAI 错误结构（SC-004）
    register_error_handlers(app)
    return app


app = create_app()