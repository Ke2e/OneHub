# OneHub 发现与事实（findings）

> 只放跨会话仍有价值的研究结论与技术事实，不放任务状态（那在 task_plan.md）。

## 项目设置

- **spec-kit v1.0.1** 已通过 `specify init . --integration claude` 初始化：`.specify/`（模板/脚本/constitution）+ `.claude/skills/`（speckit-* 命令）
- feature 目录采用顺序编号（init-options.json: `feature_numbering: sequential`）→ W1 = `specs/001-openai-compat-gateway`
- `.specify/feature.json` 记录活跃 feature 目录，/speckit-plan、/speckit-tasks 靠它定位

## 技术事实（W1 相关，供实现阶段查阅）

- **OpenAI Chat Completions 协议核心契约**：请求 `{model, messages, stream, ...}`；非流式响应含 `id/object/choices/usage`；流式为 SSE，chunk 逐块推送，终止事件为 `data: [DONE]`
- **上游 usage 语义**：DeepSeek 等渠道在流式模式下默认不在 chunk 中带 usage，需调用方在请求中显式设置 `stream_options: {"include_usage": true}` 才在最后一个 chunk 返回——这正是 /clarify Q2 要定夺的行为（网关是否替调用方注入）
- **错误协议**：OpenAI 错误体为 `{error: {message, type, param, code}}`；4xx/5xx 语义与上游状态码对齐（429 限流、401 鉴权失败等）
- **协议兼容验证标准**：OpenAI 官方 SDK 只改 `base_url` + API Key，流式/非流式均正常（PROJECT_CONTEXT 唯一硬标准）

## 流程发现

- spec-kit 的 /speckit-specify 规定 [NEEDS CLARIFICATION] 标记最多 3 个；本 feature 用了 4 个（FR-004/005/008/010），已在 spec 内说明理由（Q4 建表范围影响脚手架任务拆分），等 /clarify 统一回收
- /speckit-clarify 上限 5 问；Asize 要求一次性列出（覆盖"逐问交互"默认流程），等亲自回答
