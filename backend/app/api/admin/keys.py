"""管理面 keys CRUD（W2 任务 1）：SK-Key 生成 / 哈希存储 / 白名单 / 软删。

安全语义：
- 明文 sk-key 仅在创建瞬间返回（POST 响应），DB 只存 SHA-256 哈希 + 前 10 位 prefix
- GET 列表绝不回读 key_hash / 明文（密钥不可逆泄露面为零）
- DELETE = 软删（status → revoked）：usage_records 外键引用存在，硬删破坏引用完整性
- 全部端点按 JWT 的 tenant_id 做租户隔离（跨租户不可见/不可删）
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ModelNotFoundError
from app.core.security import require_admin
from app.models import ApiKey
from app.schemas.admin import CreateKeyRequest
from app.services.keys import generate_sk_key

router = APIRouter(prefix="/api/keys", tags=["admin"])


def _public_fields(key: ApiKey) -> dict:
    """脱敏响应字段：绝不包含 key_hash / 明文。"""
    return {
        "id": key.id,
        "name": key.name,
        "key_prefix": key.key_prefix,
        "status": key.status,
        "model_whitelist": key.model_whitelist,
        "rpm_limit": key.rpm_limit,
        "tpm_limit": key.tpm_limit,
        "expires_at": key.expires_at,
        "created_at": key.created_at,
    }


@router.post("", status_code=201)
async def create_key(
    req: CreateKeyRequest,
    request: Request,
    claims: dict = Depends(require_admin),
) -> dict:
    """创建 SK-Key：明文一次返回，哈希入库，白名单落库（任务 3 限流消费）。"""
    plaintext, key_prefix, key_hash = generate_sk_key()
    key = ApiKey(
        tenant_id=claims["tenant_id"],
        name=req.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        # status/limits 显式赋值，不依赖 DB server_default（桩/本地语义一致）
        status="active",
        rpm_limit=req.rpm_limit,
        tpm_limit=req.tpm_limit,
        model_whitelist=req.model_whitelist,
        expires_at=req.expires_at,
    )
    async with AsyncSession(request.app.state.engine) as session:
        session.add(key)
        await session.commit()
        await session.refresh(key)

    return {
        "id": key.id,
        "key": plaintext,  # 唯一一次明文交付
        **_public_fields(key),
    }


@router.get("")
async def list_keys(
    request: Request,
    claims: dict = Depends(require_admin),
) -> dict:
    """当前租户 Key 列表（脱敏：无 key_hash / 明文）。"""
    async with AsyncSession(request.app.state.engine) as session:
        rows = (
            await session.scalars(
                select(ApiKey).where(ApiKey.tenant_id == claims["tenant_id"])
            )
        ).all()
    return {"items": [_public_fields(k) for k in rows]}


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: int,
    request: Request,
    claims: dict = Depends(require_admin),
) -> Response:
    """软删：status → revoked（保 usage_records 外键引用）。租户隔离查删。"""
    async with AsyncSession(request.app.state.engine) as session:
        key = await session.scalar(
            select(ApiKey).where(
                ApiKey.id == key_id,
                ApiKey.tenant_id == claims["tenant_id"],
            )
        )
        if key is None:
            raise ModelNotFoundError(param="key")
        key.status = "revoked"
        await session.commit()
    return Response(status_code=204)