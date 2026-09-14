"""管理面 channels CRUD（W4 任务 2）：渠道管理 + 熔断实时态展示。

- GET 列表：DB 字段（name/weight/status/failure_count/opened_at）+ 从 Redis
  CircuitBreaker 读实时三态（state/opened_at 等），供前端"渠道状态可视化"。
- POST 创建：name 查重→409；api_key_encrypted 存占位（密钥加密留 W4 收尾统一加固）。
- PATCH 局部更新：weight/status/base_url 等（熔断状态由路由层驱动，管理面不直接改）。
- DELETE 硬删：usage_records.channel_id 为可空外键，若被引用则 409 拒绝
  （保引用完整性，避免破坏计费对账；停用用 PATCH status=manual_down，勿硬删）。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ModelNotFoundError
from app.core.security import require_admin
from app.models import Channel, UsageRecord
from app.schemas.admin import ChannelCreate, ChannelUpdate

router = APIRouter(prefix="/api/channels", tags=["admin"])

# 未传密钥时的占位符（W4 计费加密前统一加固）
_API_KEY_PLACEHOLDER = "env-required"


def _public_fields(ch: Channel, breaker_state: dict) -> dict:
    """渠道公共字段 + 熔断实时态（合并 DB 冗余列与 Redis 实时态）。"""
    return {
        "id": ch.id,
        "name": ch.name,
        "provider": ch.provider,
        "base_url": ch.base_url,
        "weight": ch.weight,
        "status": ch.status,
        "failure_count": ch.failure_count,
        "opened_at": ch.opened_at,
        "created_at": ch.created_at,
        # 熔断实时态（Redis，展示层只读）；DB 冗余列不随 Redis 双写
        "breaker_state": breaker_state,
    }


@router.get("")
async def list_channels(
    request: Request,
    _claims: dict = Depends(require_admin),
) -> dict:
    """渠道列表 + 各渠道熔断实时态（从 app.state.circuit_breaker 读，一次一条）。"""
    async with AsyncSession(request.app.state.engine) as session:
        channels = (await session.scalars(select(Channel))).all()
    breaker = request.app.state.circuit_breaker
    states = {ch.id: await breaker.get_state(ch.id) for ch in channels}
    return {"items": [_public_fields(ch, states[ch.id]) for ch in channels]}


@router.post("", status_code=201)
async def create_channel(
    req: ChannelCreate,
    request: Request,
    _claims: dict = Depends(require_admin),
) -> dict:
    """新增渠道；name 查重→409（表无唯一约束，查重式幂等）。"""
    async with AsyncSession(request.app.state.engine) as session:
        exists = await session.scalar(
            select(Channel).where(Channel.name == req.name)
        )
        if exists is not None:
            raise ConflictError("channel already exists")
        ch = Channel(
            name=req.name,
            provider=req.provider,
            base_url=req.base_url,
            api_key_encrypted=req.api_key_encrypted or _API_KEY_PLACEHOLDER,
            weight=req.weight,
            status=req.status,
            # failure_count/opened_at 由路由层经 Redis 熔断驱动，创建时置初值
            failure_count=0,
            opened_at=None,
        )
        session.add(ch)
        await session.commit()
        await session.refresh(ch)
    breaker = request.app.state.circuit_breaker
    return _public_fields(ch, await breaker.get_state(ch.id))


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: int,
    req: ChannelUpdate,
    request: Request,
    _claims: dict = Depends(require_admin),
) -> dict:
    """局部更新渠道；仅覆盖提供字段，其余保留。"""
    async with AsyncSession(request.app.state.engine) as session:
        ch = await session.get(Channel, channel_id)
        if ch is None:
            raise ModelNotFoundError(param="channel")
        if req.name is not None:
            ch.name = req.name
        if req.provider is not None:
            ch.provider = req.provider
        if req.base_url is not None:
            ch.base_url = req.base_url
        if req.api_key_encrypted is not None:
            ch.api_key_encrypted = req.api_key_encrypted or _API_KEY_PLACEHOLDER
        if req.weight is not None:
            ch.weight = req.weight
        if req.status is not None:
            ch.status = req.status
        await session.commit()
        await session.refresh(ch)
    breaker = request.app.state.circuit_breaker
    return _public_fields(ch, await breaker.get_state(ch.id))


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: int,
    request: Request,
    _claims: dict = Depends(require_admin),
) -> Response:
    """硬删渠道；被 usage_records 引用 → 409（保引用完整性）。不存在 → 404。"""
    async with AsyncSession(request.app.state.engine) as session:
        ch = await session.get(Channel, channel_id)
        if ch is None:
            raise ModelNotFoundError(param="channel")
        referenced = await session.scalar(
            select(UsageRecord).where(UsageRecord.channel_id == channel_id).limit(1)
        )
        if referenced is not None:
            raise ConflictError(
                "channel has usage records; revoke it (PATCH status) instead of deleting"
            )
        session.delete(ch)
        await session.commit()
    return Response(status_code=204)