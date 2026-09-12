"""Phase 4 (T016) SSE 解析与透传 —— 保护清单组件（手写，禁换库）。

SSELineParser：事件级状态机解析器（同步核心，便于单测；增量 feed 供流式消费）——
- 剥离 `data:` 前缀；忽略注释行（`:` 开头）与其他 SSE 字段（event:/id:/retry:）
- 空行分隔事件；同一事件多条 data: 行按换行拼接（SSE 规范多行 payload）
- `data: [DONE]` → done=True 停止产出（正常终止）
- 坏 JSON 事件跳过（容错，不透传给调用方，防 SDK 侧解析崩溃）
- EOF 截断（上游中断/连接断开）→ finish() 结算残余 buffer 并自然收敛，不抛异常

sse_events()：AsyncIterator[str]（httpx stream().aiter_lines() 直喂）→ 事件 dict
增量产出，供 provider.chat_stream() 逐块透传。
"""

import json
from typing import Any, AsyncIterator

_DONE = "[DONE]"


class SSELineParser:
    """SSE 行级解析：feed() 增量喂行，空行触发事件结算，[DONE]/EOF 收敛。"""

    def __init__(self) -> None:
        self._data_lines: list[str] = []
        self.done = False

    def feed(self, line: str) -> list[dict[str, Any]]:
        """喂入一行，返回本行触发的结算事件（通常为空列表）。[DONE] 后不再结算。"""
        events: list[dict[str, Any]] = []
        if self.done:
            return events
        if line == "":
            # 空行 = 事件分隔符
            events = self._settle()
            return events
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            return events  # 纯空白 / SSE 注释行（: ...）
        if stripped.startswith("data:"):
            # 剥离 "data:" 前缀并去掉前导空格；非 data 字段（event:/id:/retry:）忽略
            self._data_lines.append(stripped[len("data:"):].lstrip())
        return events

    def _settle(self) -> list[dict[str, Any]]:
        """结算 buffer 为单个事件；空 buffer / 已 done 不产生事件。"""
        if self.done or not self._data_lines:
            return []
        payload = "\n".join(self._data_lines)  # 多 data: 行按换行拼接（SSE 规范）
        self._data_lines = []
        if payload == _DONE:
            self.done = True
            return []
        try:
            return [json.loads(payload)]
        except json.JSONDecodeError:
            return []  # 坏 JSON：跳过该事件（容错，不透传）

    def finish(self) -> list[dict[str, Any]]:
        """EOF（上游中断/截断）：结算末事件残余 buffer，自然收敛不抛异常。"""
        return self._settle()

    @classmethod
    def parse_full(cls, text: str) -> list[dict[str, Any]]:
        """整段 SSE 文本 → 事件序列（单测/小文本便捷入口；长流用增量 feed+sse_events）。"""
        parser = cls()
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            events.extend(parser.feed(line))
            if parser.done:
                break
        events.extend(parser.finish())
        return events


async def sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """异步适配：AsyncIterator[str]（httpx aiter_lines）→ 事件 dict 增量产出。

    [DONE] 提前终止；行流提前结束（上游中断）结算残余后收敛——两者都不向调用方报错。
    """
    parser = SSELineParser()
    async for line in lines:
        for event in parser.feed(line):
            yield event
        if parser.done:
            return
    for event in parser.finish():
        yield event