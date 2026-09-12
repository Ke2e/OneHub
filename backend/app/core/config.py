"""应用配置：pydantic-settings 从环境变量 / .env 读取，集中管理。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OneHub 全部运行期配置。

    字段默认值面向本地开发（与 .env.example 对齐）；
    容器环境通过 compose env_file 注入同名环境变量覆盖。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL 连接串（asyncpg 异步驱动，数据库 URL 与 .env.example 一致）
    database_url: str = "postgresql+asyncpg://onehub:onehub@localhost:5432/onehub"

    # Redis 连接串：W1 仅容器就位，无业务读写（限流 W2、余额缓存 W3 启用）
    redis_url: str = "redis://localhost:6379/0"

    # 管理面 JWT 签名密钥（W2 启用；W1 仅预留）
    secret_key: str = "change-me"

    # W1 网关面固定管理 Key（Bearer 校验，W2 替换为 Key 表）
    gateway_api_key: str = "sk-gateway-dev"

    # DeepSeek 渠道密钥：运行时 env 注入，不入库不落盘，绝对不入 git
    deepseek_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """进程级单例，避免每请求重复解析环境。"""
    return Settings()