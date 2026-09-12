"""pytest 全局基建：测试环境配置覆盖 + 事件循环管理（对应 T004）。"""

import asyncio
import os

import pytest

# ==== 配置覆盖：必须在导入 app 之前设置 ====
# 测试库与开发库隔离，避免单测污染本地数据
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://onehub:onehub@localhost:5432/onehub_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SECRET_KEY", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("GATEWAY_API_KEY", "test-gateway-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")


@pytest.fixture(scope="session")
def event_loop():
    """session 级事件循环，供 async 测试复用（pytest-asyncio）。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()