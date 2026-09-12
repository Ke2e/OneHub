"""T010 OpenAI 协议 Pydantic 模型——严格对齐 contracts/openai-compat-api.md。

请求：入站接受字段（model/messages 必填），未知标准字段经 extra="allow" 透传；
响应：非流式完整对象（chat.completion）+ 流式 chunk（chat.completion.chunk）双形态。
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """对话消息：OpenAI 格式（role + content），透传语义。"""

    role: str
    content: Optional[str] = None
    name: Optional[str] = None


class StreamOptions(BaseModel):
    """流式选项：include_usage 由网关无条件注入（Q2: A 决议），入站传不传都行。"""

    include_usage: Optional[bool] = None


class ChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions 入站请求体（contracts 请求表）。

    extra="allow"：OpenAI 协议标准字段在此显式列出，其余未建模字段原样透传
    给上游（透传语义，见 contracts「temperature / ... / 等」行）。
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[Message]
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    n: Optional[int] = None
    stream_options: Optional[StreamOptions] = None


class Usage(BaseModel):
    """token 用量：非流式必返，流式在末尾 chunk（上游未返则缺省 0）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Choice(BaseModel):
    """非流式 choices[0]：完整 message + 结束原因。"""

    index: int = 0
    message: Message
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    """非流式响应主体（contracts 非流式响应节，HTTP 200）。"""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Optional[Usage] = None


class Delta(BaseModel):
    """流式增量：role 首块出现，content 逐块累计。"""

    role: Optional[str] = None
    content: Optional[str] = None


class ChunkChoice(BaseModel):
    """流式 choices[0]：delta 而非 message。"""

    index: int = 0
    delta: Delta = Delta()
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    """流式 chunk（数据事件 data: {chunk}，object 为 chat.completion.chunk）。

    usage 仅在最后一个数据 chunk 携带（include_usage 注入结果）。
    """

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice]
    usage: Optional[Usage] = None