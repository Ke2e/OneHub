"""管理面 models 定价 CRUD（W3 任务 1）：全局模型清单 + 定价 + 启停。

models 为全局共享表（无 tenant_id 列，W1 设计），模型/定价是平台级配置，
管理操作经 require_admin（管理面 JWT）鉴权即为管理身份，不做租户隔离。

DELETE 硬删：usage_records.model 是 VARCHAR（非外键引用 models），删除安全；
下架模型后客户端请求该 model 经网关 model enabled 校验自然 404。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ModelNotFoundError
from app.core.security import require_admin
from app.models import Model
from app.schemas.admin import ModelCreate, ModelUpdate

router = APIRouter(prefix="/api/models", tags=["admin"])


def _public_fields(m: Model) -> dict:
    """模型公共字段（定价/启停/渠道归属）。Numeric-Decimal → float 以 JSON 序列化。"""
    def _price(v):
        return float(v) if v is not None else None

    return {
        "id": m.id,
        "model_name": m.model_name,
        "channel_id": m.channel_id,
        "input_price": _price(m.input_price),
        "output_price": _price(m.output_price),
        "enabled": m.enabled,
    }


@router.get("")
async def list_models(
    request: Request,
    claims: dict = Depends(require_admin),
) -> dict:
    """全量模型清单（含定价）。"""
    async with AsyncSession(request.app.state.engine) as session:
        rows = (await session.scalars(select(Model))).all()
    return {"items": [_public_fields(m) for m in rows]}


@router.post("", status_code=201)
async def create_model(
    req: ModelCreate,
    request: Request,
    claims: dict = Depends(require_admin),
) -> dict:
    """新增模型定价记录；model_name 重复 → 409（表无唯一约束，查重式幂等）。"""
    async with AsyncSession(request.app.state.engine) as session:
        exists = await session.scalar(
            select(Model).where(Model.model_name == req.model_name)
        )
        if exists is not None:
            raise ConflictError(message="model already exists")
        model = Model(
            model_name=req.model_name,
            channel_id=req.channel_id,
            input_price=req.input_price,
            output_price=req.output_price,
            enabled=req.enabled,
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
    return _public_fields(model)


@router.patch("/{model_id}")
async def update_model(
    model_id: int,
    req: ModelUpdate,
    request: Request,
    claims: dict = Depends(require_admin),
) -> dict:
    """局部更新模型定价/启停；仅覆盖提供字段，其余保留。"""
    async with AsyncSession(request.app.state.engine) as session:
        model = await session.get(Model, model_id)
        if model is None:
            raise ModelNotFoundError(param="model")
        if req.model_name is not None:
            model.model_name = req.model_name
        if req.channel_id is not None:
            model.channel_id = req.channel_id
        if req.input_price is not None:
            model.input_price = req.input_price
        if req.output_price is not None:
            model.output_price = req.output_price
        if req.enabled is not None:
            model.enabled = req.enabled
        await session.commit()
        await session.refresh(model)
    return _public_fields(model)


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    request: Request,
    claims: dict = Depends(require_admin),
) -> Response:
    """硬删模型；不存在 → 404。"""
    async with AsyncSession(request.app.state.engine) as session:
        model = await session.get(Model, model_id)
        if model is None:
            raise ModelNotFoundError(param="model")
        session.delete(model)
        await session.commit()
    return Response(status_code=204)