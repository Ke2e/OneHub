"""T007 种子脚本：注入 W1 最小渠道与模型集，支持重复执行（幂等 upsert）。

数据依据（data-model.md / PROJECT_CONTEXT 第 5 节）：
- channels 1 行 deepseek-main，api_key_encrypted 占位 `env-injected`（D2：真实密钥运行期 env 注入）
- models 2 行 deepseek-chat / deepseek-reasoner，均挂 deepseek-main

2026-09-12（Asize 决策）：真实接入渠道为 SenseAudio 开放平台（.env BASE_URL/API_KEY），
其可用模型 ID 与官方 DeepSeek 不同——种子模型更新为该平台实际存在的 LLM：
    deepseek-v4-flash-0731（DeepSeek 系） / senseaudio-s2（平台旗舰）
渠道 base_url 同步改为真实可解析地址（W1 转发实际走 settings.env，渠道表 base_url
供未来 W4 路由读取）。

DDL 中 channels.name / models.model_name 均无唯一约束，故不用 ON CONFLICT；
改为"查重 → 无则插入，有则同步核心字段"，事务内完成，重复执行零副作用。

用法：`uv run python scripts/seed.py`（URL 走 app.core.config Settings，环境变量优先）
"""

import asyncio
import sys
from pathlib import Path

# 脚本位于仓库根 scripts/ 下，需将 backend 加入 sys.path 才能导入 app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models import Channel, Model

CHANNEL = {
    "name": "deepseek-main",
    "provider": "deepseek",
    # 真实可用地址（SenseAudio 开放平台，2026-09-12 Asize 决策；/v1 为 OpenAI 兼容前缀）
    "base_url": "https://api.senseaudio.cn/v1",
    "api_key_encrypted": "env-injected",
    "weight": 10,
}

# W4 任务 1：备份渠道（同名模型第二渠道，占位 key，权重更低 → 仅故障时承接）
CHANNEL_BACKUP = {
    "name": "deepseek-backup",
    "provider": "deepseek",
    "base_url": "https://api.senseaudio.cn/v1",
    "api_key_encrypted": "env-injected",
    "weight": 5,
}

CHANNELS = [CHANNEL, CHANNEL_BACKUP]

MODELS = [
    {"model_name": "deepseek-v4-flash-0731", "channel_id": None},
    {"model_name": "senseaudio-s2", "channel_id": None},
]

# 备份渠道挂载的模型变体：与非唯一 model_name 的行并存，构成同一模型的多渠道候选
BACKUP_MODEL_NAMES = ["deepseek-v4-flash-0731"]


async def _upsert_channel(session: AsyncSession, channel: dict) -> int:
    """按 name 查重插入/同步渠道；同行已存在则刷新核心字段。返回渠道 id。"""
    existing = (
        await session.execute(select(Channel).where(Channel.name == channel["name"]))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Channel(**channel))
        await session.flush()
        return (
            await session.execute(select(Channel).where(Channel.name == channel["name"]))
        ).scalar_one().id
    for field, value in channel.items():
        if field != "name":
            setattr(existing, field, value)
    return existing.id


async def _upsert_models(session: AsyncSession, channel_ids: dict[str, int]) -> None:
    """按 (model_name, channel_id) 查重：同模型可挂多渠道（backup 变体），互不影响。"""
    existing = {
        (r.model_name, r.channel_id): r
        for r in (await session.execute(select(Model))).scalars().all()
    }
    pairs = [(m["model_name"], channel_ids["deepseek-main"]) for m in MODELS]
    pairs += [(name, channel_ids["deepseek-backup"]) for name in BACKUP_MODEL_NAMES]
    for name, cid in pairs:
        if (name, cid) not in existing:
            session.add(Model(model_name=name, channel_id=cid))


async def seed() -> None:
    """幂等种子：重复执行后渠道/模型仍是各自唯一一份。"""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with AsyncSession(engine) as session:
            channel_ids = {
                c["name"]: await _upsert_channel(session, c) for c in CHANNELS
            }
            await _upsert_models(session, channel_ids)
            await session.commit()
            print(f"[seed] ensured channels={[c['name'] for c in CHANNELS]} "
                  f"models={[m['model_name'] for m in MODELS]} + backup variants")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())