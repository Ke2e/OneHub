"""管理面仪表盘 + 用量查询（W4 任务 2）：KPI 概览 / 渠道分布 / 用量明细分页。

数据源全部为现有 7 表（usage_records / balances / channels），只读不写，挂
require_admin（管理面 JWT）。聚合逻辑在 services/dashboard.py 纯 Python 完成，
端点仅负责「拉基础行 → 调聚合」，离线测试用假表桩即可覆盖。

端点：
- GET /api/dashboard/overview     → KPI（请求量 / token / 成本 / 活跃渠道 / 余额）
- GET /api/dashboard/channels     → 各渠道聚合（ECharts 分流/延迟）
- GET /api/usage/logs             → 用量明细分页（按 created_at 倒序）
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.models import Balance, Channel, UsageRecord
from app.services.dashboard import paginate_logs, summarize_by_channel, summarize_overview

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/dashboard/overview")
async def dashboard_overview(
    request: Request,
    _claims: dict = Depends(require_admin),
) -> dict:
    """KPI 面板：全量 + 24h 请求量/token/成本 + 活跃渠道数 + 租户余额。"""
    async with AsyncSession(request.app.state.engine) as session:
        records = (await session.scalars(select(UsageRecord))).all()
        balances = (await session.scalars(select(Balance))).all()
        channels = (await session.scalars(select(Channel))).all()
    return summarize_overview(records, balances, channels)


@router.get("/dashboard/channels")
async def dashboard_channels(
    request: Request,
    _claims: dict = Depends(require_admin),
) -> dict:
    """各渠道用量聚合：请求数 / token / 成本 / 平均延迟（ECharts 数据源）。"""
    async with AsyncSession(request.app.state.engine) as session:
        records = (await session.scalars(select(UsageRecord))).all()
    return {"items": summarize_by_channel(records)}


@router.get("/usage/logs")
async def usage_logs(
    request: Request,
    model: Optional[str] = None,
    api_key_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
    _claims: dict = Depends(require_admin),
) -> dict:
    """用量明细分页：可选按 model / api_key_id 过滤，按 created_at 倒序。"""
    limit = min(max(limit, 1), 200)  # 分页上限收敛，防滥用
    offset = max(offset, 0)
    async with AsyncSession(request.app.state.engine) as session:
        stmt = select(UsageRecord)
        if model:
            stmt = stmt.where(UsageRecord.model == model)
        if api_key_id is not None:
            stmt = stmt.where(UsageRecord.api_key_id == api_key_id)
        records = (await session.scalars(stmt)).all()
    return paginate_logs(records, limit, offset)