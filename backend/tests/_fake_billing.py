"""离线 BalanceService 桩（W3 任务 1 网关测试专用，对齐 _fake_db 风格）。

默认 precheck 放行（不断言/不触真实 Redis），供既有网关测试 override get_billing
消除 lifespan 真 Redis 连接依赖；test_gateway_billing.py 用可编程版本验证 402。
"""

from app.core.errors import InsufficientBalanceError


class FakeBilling:
    """记录 precheck 调用 + 可编程是否余额不足（抛 402）。"""

    def __init__(self, insufficient: bool = False):
        self.insufficient = insufficient
        self.calls: list[tuple] = []

    async def precheck(self, session, tenant_id, cost):
        self.calls.append((tenant_id, cost))
        if self.insufficient:
            raise InsufficientBalanceError()