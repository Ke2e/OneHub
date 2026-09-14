"""W4 任务 2：管理台仪表盘——聚合纯函数 + 端点离线测试。

- summarize_overview / summarize_by_channel / paginate_logs：纯函数，SimpleNamespace
  构造记录直接对拍（延续 _refill / _decide_allow 纯函数对拍风格）。
- 端点：FakeSession 桩（塞 UsageRecord/Balance/Channel），require_admin JWT 头。
"""

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.admin.dashboard as dash_module
from app.main import create_app
from app.models import Balance, Channel, UsageRecord
from app.services.dashboard import paginate_logs, summarize_by_channel, summarize_overview
from tests._fake_db import FakeSession

from decimal import Decimal


def _rec(**kw):
    """构造带默认最小字段的 usage 记录（SimpleNamespace 供纯函数测试）。"""
    base = dict(
        id=kw.get("id", 1),
        request_id=uuid4(),
        api_key_id=None,
        channel_id=None,
        model="m1",
        status_code=200,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=100,
        cost=Decimal("0.5"),
        created_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _jwt_header():
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(sub='1', tenant_id=1)}"}


# ── service 纯函数 ──

def test_summarize_overview_kpis():
    """overview：全量/24h 请求量、token、成本、活跃渠道、余额。"""
    now = datetime.now(timezone.utc)
    old = _rec(id=1, prompt_tokens=10, completion_tokens=5, cost=Decimal("0.5"),
               created_at=now - timedelta(hours=30))
    fresh = _rec(id=2, prompt_tokens=20, completion_tokens=10, cost=Decimal("1.2"),
                 created_at=now - timedelta(hours=1), latency_ms=200)
    channels = [
        SimpleNamespace(status="healthy"),
        SimpleNamespace(status="healthy"),
        SimpleNamespace(status="manual_down"),
    ]
    balances = [SimpleNamespace(tenant_id=1, balance=Decimal("99.5"))]
    kpi = summarize_overview([old, fresh], balances, channels, now=now)
    assert kpi["total_requests"] == 2
    assert kpi["requests_24h"] == 1  # old 是 30h 前，不在 24h 窗口
    assert kpi["total_tokens"] == 45
    assert kpi["tokens_24h"] == 30
    assert kpi["total_cost"] == 1.7
    assert kpi["cost_24h"] == 1.2
    assert kpi["active_channels"] == 2
    assert kpi["channel_count"] == 3
    assert kpi["tenant_balances"] == [{"tenant_id": 1, "balance": 99.5}]


def test_summarize_by_channel_aggregates():
    """by_channel：请求数 / token / 成本 / 平均延迟（含 None 延迟不计分母）。"""
    now = datetime.now(timezone.utc)
    rows = [
        _rec(id=1, channel_id=1, prompt_tokens=10, completion_tokens=5, cost=Decimal("0.5"), latency_ms=100),
        _rec(id=2, channel_id=1, prompt_tokens=20, completion_tokens=10, cost=Decimal("1.2"), latency_ms=None),
        _rec(id=3, channel_id=2, prompt_tokens=30, completion_tokens=5, cost=Decimal("0.3"), latency_ms=50),
    ]
    out = summarize_by_channel(rows)
    assert len(out) == 2
    ch1 = next(o for o in out if o["channel_id"] == 1)
    ch2 = next(o for o in out if o["channel_id"] == 2)
    assert ch1["requests"] == 2
    assert ch1["tokens"] == 45
    assert ch1["cost"] == 1.7
    assert ch1["avg_latency_ms"] == 100.0  # 只有 1 条含 latency
    assert ch2["requests"] == 1
    assert ch2["avg_latency_ms"] == 50.0


def test_paginate_logs_desc_and_slice():
    """logs：按 created_at 倒序 + [offset, offset+limit) 切片，返回 total。"""
    now = datetime.now(timezone.utc)
    rows = [_rec(id=i, created_at=now - timedelta(hours=i)) for i in range(5)]
    out = paginate_logs(rows, limit=2, offset=1)
    assert out["total"] == 5
    assert [it["id"] for it in out["items"]] == [1, 2]  # 倒序：id=0 最新被跳过
    assert out["offset"] == 1
    assert out["limit"] == 2


# ── 端点（FakeSession 桩 + require_admin） ──

def _shared_factory(initial=None):
    def factory(*args, **kwargs):
        return factory.shared
    factory.shared = FakeSession(initial=initial or [])
    return factory


def _make_factory_initial():
    now = datetime.now(timezone.utc)
    return [
        UsageRecord(
            request_id=uuid4(), model="m1", channel_id=1,
            prompt_tokens=10, completion_tokens=5, latency_ms=100,
            cost=Decimal("0.5"), created_at=now - timedelta(hours=1),
        ),
        Balance(tenant_id=1, balance=Decimal("88.0"), version=0),
        Channel(name="c1", provider="deepseek", base_url="https://x/v1",
                api_key_encrypted="env-required", status="healthy", weight=10),
        Channel(name="c2", provider="deepseek", base_url="https://y/v1",
                api_key_encrypted="env-required", status="manual_down", weight=5),
    ]


def test_dashboard_overview_endpoint(client_from, monkeypatch):
    factory = _shared_factory(_make_factory_initial())
    monkeypatch.setattr(dash_module, "AsyncSession", factory)
    resp = client_from.get("/api/dashboard/overview", headers=_jwt_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 1
    assert body["requests_24h"] == 1
    assert body["active_channels"] == 1  # 只有 c1 healthy
    assert body["channel_count"] == 2
    assert body["tenant_balances"] == [{"tenant_id": 1, "balance": 88.0}]


def test_dashboard_channels_endpoint(client_from, monkeypatch):
    factory = _shared_factory(_make_factory_initial())
    monkeypatch.setattr(dash_module, "AsyncSession", factory)
    resp = client_from.get("/api/dashboard/channels", headers=_jwt_header())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items == [{"channel_id": 1, "requests": 1, "tokens": 15,
                      "cost": 0.5, "avg_latency_ms": 100.0}]


def test_usage_logs_endpoint_paginates(client_from, monkeypatch):
    factory = _shared_factory(_make_factory_initial())
    monkeypatch.setattr(dash_module, "AsyncSession", factory)
    resp = client_from.get("/api/usage/logs?limit=1&offset=0", headers=_jwt_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["model"] == "m1"
    assert item["cost"] == 0.5
    assert item["channel_id"] == 1


def test_dashboard_requires_jwt(client_from):
    """无 JWT → 401 authentication_error（管理面契约）。"""
    resp = client_from.get("/api/dashboard/overview")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


@pytest.fixture
def client_from():
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c