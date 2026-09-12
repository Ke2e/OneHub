"""Alembic 迁移环境：async 引擎注入（T006）。

URL 来源优先级：进程环境变量/`.env` → Settings 默认值（localhost）。
容器内由 compose env_file 注入 pg 内网地址；本地开发直连 localhost 映射端口。
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import get_settings
from app.models import Base

# config：Alembic Config 对象，提供 .ini 内配置访问
config = context.config

# Python logging 配置（.ini 的 [loggers] 段）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 依赖的 target_metadata：全量 7 表
target_metadata = Base.metadata

# URL 注入：Settings（pydantic-settings 已含环境变量优先级）决定连接目标
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """离线模式：仅渲染 SQL 不连库（--sql 分支）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式：创建 async 引擎，单次维护连接执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口：包一个事件循环跑 async 迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()