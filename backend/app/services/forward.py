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


class UsageTracker:
    """流式 usage 提取（T019，保护清单手写）：在事件级纯透传之上加用量语义。

    - 上游 chunk 自带 usage（include_usage 注入生效）：原样透传该事件，提取 usage 值
    - 上游未返 usage（渠道不支持/注入未生效/EOF 截断）：流收敛时合成一个
      usage=0 的兜底 chunk 事件——调用方零配置总能拿到 usage 对象（contracts
      「最后一个数据 chunk 携带 usage」「上游异常未返 usage：缺省 0，不报错」）
    - 合成事件位次：finish() 产出 → 位于流的最后一个（gateway 层 [DONE] 之前）
    """

    def __init__(self) -> None:
        self.usage: dict[str, int] | None = None  # 提取结果；None = 上游未返
        self._sample: dict[str, Any] = {}  # 首个 chunk 的 id/created/model 采样

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """喂入一个事件：原样透传（[event]），同时采样与提取 usage。"""
        if isinstance(event, dict):
            if event.get("usage"):
                self.usage = event["usage"]
            elif not self._sample and event.get("id"):
                # 合成兜底事件需要与流同源的 id/created/model（OpenAI chunk 结构）
                self._sample = {
                    "id": event.get("id", ""),
                    "created": event.get("created", 0),
                    "model": event.get("model", ""),
                }
        return [event]

    def finish(self) -> list[dict[str, Any]]:
        """流收敛：未提取到 usage → 合成 usage=0 兜底事件（choices 空，OpenAI 语义）。"""
        if self.usage is not None:
            return []
        return [
            {
                **self._sample,
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        ]


async def with_usage(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """异步适配：事件 dict 流（sse_events 产出）→ 增量透传 + 流尾 usage 兜底。

    上游已返 usage：逐事件原样透传，不做追加；未返：最后一个产出为合成 usage=0
    事件（调用方在末尾 chunk 总能看到 usage，SC-002 形式保证）。
    """
    tracker = UsageTracker()
    async for event in events:
        for out in tracker.feed(event):
            yield out
    for out in tracker.finish():
        yield out