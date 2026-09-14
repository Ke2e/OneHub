"""W4 任务 2：管理台仪表盘聚合纯函数（overview KPI / 渠道分布 / 用量分页）。

设计：聚合不写死在 SQL 层（BETWEEN / SUM / GROUP BY / ORDER LIMIT 依赖
真实数据库方言，离线 FakeSession 桩也无法模拟），改为「端点拉基础行 →
本模块纯 Python 聚合」。真实数据量下开销可接受（管理台展示维度），
且纯函数可离线单测对拍，延续项目「核心逻辑可测」惯例。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional


def daily_cutoff(now: Optional[datetime] = None, hours: int = 24) -> datetime:
    """24h 时间下界：now - 24h（默认 UTC now）。"""
    now = now or datetime.now(timezone.utc)
    return now - timedelta(hours=hours)


def _cost(v) -> float:
    return float(v) if v is not None else 0.0


def _tokens(r) -> int:
    return (r.prompt_tokens or 0) + (r.completion_tokens or 0)


def summarize_overview(records: list, balances: list, channels: list, now: Optional[datetime] = None) -> dict:
    """KPI 汇总：全量 + 24h 请求量/token/成本、活跃渠道数、租户余额。

    records: usage_records 行；balances: balances 行；channels: channels 行。
    返回前端 KPI 面板直接消费的扁平 dict。
    """
    cutoff = daily_cutoff(now)
    recent = [r for r in records if r.created_at and r.created_at >= cutoff]
    return {
        "total_requests": len(records),
        "requests_24h": len(recent),
        "total_tokens": sum(_tokens(r) for r in records),
        "tokens_24h": sum(_tokens(r) for r in recent),
        "total_cost": round(sum(_cost(r.cost) for r in records), 6),
        "cost_24h": round(sum(_cost(r.cost) for r in recent), 6),
        "active_channels": sum(1 for c in channels if c.status == "healthy"),
        "channel_count": len(channels),
        "tenant_balances": [
            {"tenant_id": b.tenant_id, "balance": float(b.balance)} for b in balances
        ],
    }


def summarize_by_channel(records: list) -> list:
    """按 channel_id 聚合用量：请求数 / token / 成本 / 平均延迟（ECharts 数据源）。"""
    agg: dict[int, dict] = {}
    for r in records:
        cid = r.channel_id
        a = agg.setdefault(cid, {"requests": 0, "tokens": 0, "cost": 0.0, "latency_sum": 0, "latency_n": 0})
        a["requests"] += 1
        a["tokens"] += _tokens(r)
        a["cost"] += _cost(r.cost)
        if r.latency_ms is not None:
            a["latency_sum"] += r.latency_ms
            a["latency_n"] += 1
    return [
        {
            "channel_id": cid,
            "requests": v["requests"],
            "tokens": v["tokens"],
            "cost": round(v["cost"], 6),
            "avg_latency_ms": (
                round(v["latency_sum"] / v["latency_n"], 1) if v["latency_n"] else None
            ),
        }
        for cid, v in sorted(agg.items())
    ]


def _log_item(r) -> dict:
    return {
        "id": r.id,
        "request_id": str(r.request_id),
        "api_key_id": r.api_key_id,
        "channel_id": r.channel_id,
        "model": r.model,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "latency_ms": r.latency_ms,
        "status_code": r.status_code,
        "cost": round(_cost(r.cost), 6),
        "created_at": r.created_at,
    }


def paginate_logs(records: list, limit: int, offset: int) -> dict:
    """用量明细分页：按 created_at 倒序 → 按 [offset, offset+limit) 切片。

    records 应为已按 query 过滤（model / api_key_id）的行。返回 {items, total}。
    """
    rows = sorted(
        records,
        key=lambda r: r.created_at.timestamp() if r.created_at else 0.0,
        reverse=True,
    )
    total = len(rows)
    return {
        "items": [_log_item(r) for r in rows[offset : offset + limit]],
        "total": total,
        "offset": offset,
        "limit": limit,
    }