"""T015 SSE 解析器单测（保护清单，测试先行 RED → T016 实现转 GREEN）。

覆盖（tasks.md T015 要求五能力 + 手写解析器完整度）：
- data: 前缀剥离（fixture 综合流）
- 空行分隔事件（fixture 综合流）
- `[DONE]` 终止——之后的行不再产出（fixture 综合流）
- 坏 JSON 行容错——跳过该事件，前后事件不受影响（fixture + 内联交叉验证）
- 上游中断流截断——EOF 无 `[DONE]` 自然收敛不抛异常（内联）
- 同事件多 data: 行拼接（SSE 规范支持，体现手写 vs 换库）
"""

from pathlib import Path

from app.services.forward import SSELineParser

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sse_stream.txt"


def _load_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_data_prefix_blank_separator_done_and_bad_json_skipped():
    """fixture 综合流：剥离 data: 前缀、空行分隔、[DONE] 终止、坏 JSON 行跳过。"""
    events = SSELineParser.parse_full(_load_fixture())

    # 头行注释与坏 JSON 行不产生事件；3 个合法事件按空行分隔独立产出（含中文透传）
    assert [e["id"] for e in events] == ["cmpl-1", "cmpl-1", "cmpl-2"]
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    assert events[1]["choices"][0]["delta"]["content"] == "你好，世界"

    # [DONE] 终止：之后的行（cmpl-3）不再产出
    assert all(e["id"] != "cmpl-3" for e in events)


def test_multi_data_lines_join_into_one_event():
    """同一事件多条 data: 行按换行拼接（SSE 规范；payload 跨行仍是合法 JSON）。"""

    def _parse_multi() -> list[dict]:
        data_lines = [
            'data: {"id": "multi",',
            'data: "ok": true}',
            "",
        ]
        parser = SSELineParser()
        events: list[dict] = []
        for line in data_lines:
            events.extend(parser.feed(line))
        events.extend(parser.finish())
        return events

    events = _parse_multi()
    assert events == [{"id": "multi", "ok": True}]


def test_bad_json_event_skipped_and_neighbors_kept():
    """坏 JSON 独立事件被跳过，前后合法事件完整保留（容错不透传）。"""
    stream = (
        'data: {"id":"a"}\n'
        "\n"
        "data: oh no, this is not json\n"
        "\n"
        'data: {"id":"b"}\n'
        "\n"
    )
    events = SSELineParser.parse_full(stream)
    assert [e["id"] for e in events] == ["a", "b"]


def test_truncated_stream_without_done_converges():
    """上游中断截断：EOF 无 [DONE]、末事件无收尾空行 —— 正常产出全部事件且不抛异常。"""
    stream = 'data: {"id":"x"}\n\n' 'data: {"id":"y"}\n'
    events = SSELineParser.parse_full(stream)
    assert [e["id"] for e in events] == ["x", "y"]


def test_comment_lines_and_whitespace_ignored():
    """注释行与纯空白行不参与事件（不结算、不污染 buffer）。"""
    stream = (
        ": keep-alive comment\n"
        "   \n"
        'data: {"id":"only"}\n'
        "event: message\n"  # 非 data 字段忽略
        "\n"
    )
    events = SSELineParser.parse_full(stream)
    assert [e["id"] for e in events] == ["only"]