"""pytest 全局基建：测试环境配置覆盖（对应 T004）。

事件循环交由 pytest-asyncio 0.26+ 管理（pyproject 已设
asyncio_default_fixture_loop_scope="function"）；自定义 event_loop
fixture 已被官方弃用，删掉可避免 async 测试偶发拿不到 current loop。
"""

import os

# ==== 配置覆盖：必须在导入 app 之前设置 ====
# 测试库与开发库隔离，避免单测污染本地数据
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://onehub:onehub@localhost:5432/onehub_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SECRET_KEY", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")