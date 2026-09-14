"""离线 UsageProducer 桩（W3 任务 2 网关测试专用，对齐 _fake_billing 风格）。

默认 emit 接收并不触发真实 Redis；供既有网关测试 override get_usage_producer
消除 lifespan 真 Redis 连接依赖。可编程记录调用，供断言网关是否发出用量事件。
"""


class FakeUsageProducer:
    """记录 emit 调用 + 可编程发射结果（网关测试断言 XADD 语义）。"""

    def __init__(self):
        self.emitted: list[dict] = []

    async def emit(self, event: dict) -> str:
        self.emitted.append(event)
        return "1-0"

    async def aclose(self):
        pass