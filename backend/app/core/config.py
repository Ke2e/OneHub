"""应用配置：pydantic-settings 从环境变量 / .env 读取，集中管理。"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：本文件位于 backend/app/core/，向上 3 级即仓库根（.env 所在地）。
# 用绝对路径而非相对 cwd，保证从 backend/ 或仓库根任意目录运行都能读到。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """OneHub 全部运行期配置。

    字段默认值面向本地开发；容器环境通过 compose env_file 注入同名
    环境变量覆盖。渠道 key 支持双键名（见 deepseek_api_key 注释）。
    """

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
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

    # DeepSeek 渠道地址：优先 DEEPSEEK_BASE_URL，回退 .env 实际键名 BASE_URL
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("DEEPSEEK_BASE_URL", "BASE_URL"),
    )

    # DeepSeek 渠道密钥：双键名兼容——约定 DEEPSEEK_API_KEY 或 .env 实际键名 API_KEY。
    # 运行时 env 注入，不入库不落盘，绝对不入 git，绝不打印值。
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "API_KEY"),
    )


@lru_cache
def get_settings() -> Settings:
    """进程级单例，避免每请求重复解析环境。"""
    return Settings()