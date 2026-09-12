"""OneHub 应用入口：应用工厂 + lifespan 管理 SQLAlchemy async 引擎生命周期。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.core.errors import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时创建 async 引擎，关闭时释放连接池。

    create_async_engine 是惰性连接，应用可在 pg 未就绪时启动；
    实际查询时才建连（池预检 pool_pre_ping 应对连接回收）。
    """
    settings = get_settings()
    engine: AsyncEngine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    app.state.engine = engine
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    """应用工厂：W1 仅网关骨架，路由注册在后续 Phase 接入。"""
    app = FastAPI(
        title="OneHub Gateway",
        description="OpenAI 协议兼容的多模型 LLM API 聚合网关",
        version="0.1.0",
        docs_url="/docs",
        lifespan=lifespan,
    )
    # T008：统一错误出口——所有非 2xx 重塑为 OpenAI 错误结构（SC-004）
    register_error_handlers(app)
    return app


app = create_app()