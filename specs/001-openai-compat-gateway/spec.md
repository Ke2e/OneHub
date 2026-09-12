# Feature Specification: OpenAI 协议兼容网关（W1：协议兼容层 + 单渠道转发）

**Feature Branch**: `001-openai-compat-gateway`

**Created**: 2026-09-10

**Status**: Draft

**Input**: User description: "按 docs/PROJECT_CONTEXT.md 第 7 节 W1 计划：协议兼容层 + 单渠道转发——OpenAI 兼容端点 + DeepSeek Provider、SSE 流式透传 + delta 累计 usage、脚手架（含 pg/redis 容器化环境）。"

## Clarifications

### Session 2026-09-11

- Q: W1 网关端点要不要鉴权？ → A: 环境变量固定管理 Key + Bearer 校验（单管理 Key），W2 替换为 Key 表（Q1: B）
- Q: 流式 usage 的保证范围？ → A: 网关始终主动注入 include_usage；上游无 usage 时 W1 返回缺省值（0），tokenizer 估算推迟至 W3（Q2: A）
- Q: /v1/models 列表数据来源？ → A: 读数据库 models 表，W1 全量建表 + 种子数据（Q3: B）
- Q: W1 Alembic 建表范围？ → A: 全量 7 表一次建齐（Q4: A）
- Q: 协议兼容验收用哪个 OpenAI SDK？ → A: JS/TS SDK 主验（开发者主力语言，且与后端语言解耦、验证更客观），openai-python 冒烟作加分项（Q5: B）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 用 OpenAI SDK 非流式对话 (Priority: P1)

作为使用 OpenAI 官方 SDK 的开发者，我把 `base_url` 指向本网关后，发起一次非流式 Chat Completions 请求，收到与 OpenAI 官方完全相同格式的完整回复（含 `choices` 与 `usage` 字段）。除 `base_url` 与 API Key 外，我不需要修改 SDK 调用代码的任何其他部分。

**Why this priority**: 协议兼容性是本项目唯一硬标准与验证里程碑（PROJECT_CONTEXT 第 2 节），非流式是最小可验证场景。

**Independent Test**: 用 OpenAI SDK 只改 `base_url` 发一条消息，收到正常对话回复即通过。

**Acceptance Scenarios**:

1. **Given** 网关已启动且 DeepSeek 渠道可用，**When** 调用方以非流式方式 POST `/v1/chat/completions` 发送对话，**Then** 网关返回 200，响应体符合 OpenAI Chat Completions 响应结构（`id`、`object`、`choices`、`usage` 均存在且类型正确）
2. **Given** 上游渠道暂时不可用，**When** 调用方发起同样请求，**Then** 网关返回 OpenAI 协议格式的错误响应（含 `error.message` 等标准字段），而不是内部堆栈或网关私有错误结构

---

### User Story 2 - 用 OpenAI SDK 流式对话 (Priority: P1)

作为同一开发者，我在请求中传 `stream: true`，收到逐块推送的 SSE 流：每个 chunk 均为 OpenAI 流式 chunk 格式，内容按序到达，流以 `[DONE]` 结束。网关不缓冲整个响应，调用方能实时看到文字逐段出现。

**Why this priority**: 流式是 LLM 网关的主场景，也是后续 delta 累计 usage 与 W3 计费链路的基础。

**Independent Test**: SDK 以流式模式调用，逐块收到增量内容，正常结束不报错即通过。

**Acceptance Scenarios**:

1. **Given** 网关与渠道可用，**When** 调用方请求 `stream: true`，**Then** 网关以 `text/event-stream` 返回 SSE 流，chunk 结构符合 OpenAI 流式协议，且以 `data: [DONE]` 事件结束
2. **Given** 流式传输中上游连接中断，**When** 调用方正在消费流，**Then** 网关以协议兼容的方式终止流（不让调用方挂死等待）

---

### User Story 3 - 流式结束后拿到本次请求的 token 用量 (Priority: P1)

作为关注成本的调用方，流式对话结束后，我能拿到本次请求的 token 用量（`prompt_tokens` / `completion_tokens`），供本地计费统计使用。我不需要为此传任何特殊参数。

**Why this priority**: W1 显式验收标准（"流式结束 usage 事件有 token 数"），且是 W3 计费引擎的输入信号。

**Independent Test**: 完成一次流式对话后检查最后一个含 usage 的 chunk，token 数为非零正整数即通过。

**Acceptance Scenarios**:

1. **Given** 调用方发起流式请求（未传任何用量相关参数），**When** 流结束，**Then** 网关已主动向上游请求用量，流末尾 chunk 携带 usage，`prompt_tokens` 与 `completion_tokens` 为非零正整数
2. **Given** 上游异常未返回 usage，**When** 流结束，**Then** 网关返回缺省值（0）并在网关侧记录该情况，不向调用方报错（tokenizer 本地估算属 W3 范围）

---

### User Story 4 - 查询可用模型列表 (Priority: P2)

作为调用方，我通过 `GET /v1/models` 获取网关当前可用的模型 ID 列表，用于在 SDK 中选择合法的 `model` 参数值。

**Why this priority**: OpenAI SDK 及多数客户端初始化时会探测模型列表，缺失会导致兼容性缺口，但非对话主链路。

**Independent Test**: GET `/v1/models` 返回 200 且 `data` 数组中包含至少一个可用模型即通过。

**Acceptance Scenarios**:

1. **Given** 网关已配置 DeepSeek 渠道与其模型，**When** 调用方 GET `/v1/models`，**Then** 返回 OpenAI 格式的模型列表（`object: "list"`，`data` 数组含模型 ID）
2. **Given** 调用方请求了模型列表，**When** 网关决定列表内容，**Then** 列表数据来源于数据库 models 表（W1 全量建表并灌入种子数据）

---

### User Story 5 - 一键起本地开发环境 (Priority: P3)

作为本项目的开发者，我在新机器上克隆仓库后，用一条命令启动完整的本地环境（网关 + 数据库 + 缓存），并能在浏览器打开交互式 API 文档确认服务健康。

**Why this priority**: 是 W1 其余故事的运行底座，但对协议兼容性验证而言属于支撑性需求。

**Independent Test**: 一条命令启动后，交互式 API 文档页面返回 200 且可访问即通过。

**Acceptance Scenarios**:

1. **Given** 仓库已克隆且 Docker 已安装，**When** 开发者执行统一启动命令，**Then** 网关、PostgreSQL、Redis 全部启动并健康，交互式 API 文档可通过 `/docs` 访问
2. **Given** 本地环境此前已启动过，**When** 开发者再次执行启动命令，**Then** 环境可重复启动，无手工清理残留数据的步骤

---

### Edge Cases

- **When** 请求体不符合 OpenAI 协议（缺 `model` / `messages` 字段、类型错误）→ 网关按 OpenAI 协议返回 4xx 校验错误，不透传给上游
- **When** 请求的 `model` 不在网关可用列表中 → 返回 OpenAI 格式的模型不存在错误（而非 500）
- **When** 上游返回错误（鉴权失败 / 限流 / 超载）→ 错误映射为 OpenAI 错误格式，HTTP 状态码语义保留（如上游 429 → 网关 429）
- **When** 调用方在流式过程中主动断开连接 → 网关感知并终止对上游的转发，不留孤儿连接
- **When** 上游响应显著慢于预期 → 网关设置上游超时，超时后向调用方返回协议兼容的错误而非无限等待
- **When** 调用方 Bearer Key 缺失或与配置不符 → 返回 OpenAI 风格 401 错误（策略见 FR-008）

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 网关 MUST 在 `/v1/chat/completions` 端点接受符合 OpenAI Chat Completions 协议的请求（含 `model`、`messages`、`stream`、`temperature` 等标准字段）
- **FR-002**: 非流式请求（`stream: false` 或缺省）MUST 返回符合 OpenAI 响应结构的完整结果，含 `choices` 与 `usage`
- **FR-003**: 流式请求（`stream: true`）MUST 以 SSE 逐块转发，每个 chunk 符合 OpenAI 流式协议，流以 `[DONE]` 事件结束
- **FR-004**: 网关 MUST 对每次完成的请求统计 token 用量（`prompt_tokens` / `completion_tokens`）；流式场景下网关主动向上游请求用量，保证流末尾零配置可得 usage；上游异常未返回时返回缺省值（0），本地 tokenizer 估算属 W3 范围
- **FR-005**: `GET /v1/models` MUST 返回 OpenAI 格式的可用模型列表，数据来源于数据库 models 表（W1 全量建表 + 种子数据）
- **FR-006**: 网关 MUST 将对话请求转发至单一可配置的上游渠道（DeepSeek），渠道地址与密钥通过环境变量注入，不硬编码
- **FR-007**: 网关 MUST 将上游错误与非 OpenAI 格式错误映射为 OpenAI 协议错误结构（`error.message` / `error.type` / `error.code`）返回调用方
- **FR-008**: W1 网关面端点采用环境变量固定管理 Key + Bearer 校验（单一管理 Key，与渠道 Key 相互独立）；完整多租户 Key 体系属 W2，届时无缝替换
- **FR-009**: 项目 MUST 提供一键启动的本地开发环境（网关 + PostgreSQL + Redis 容器化编排），启动后交互式 API 文档可访问
- **FR-010**: 数据库表结构 MUST 按 PROJECT_CONTEXT 第 5 节 DDL 生成迁移，W1 一次性全量建 7 表（tenants/users/api_keys/channels/models/usage_records/balances）
- **FR-011**: 网关面鉴权（FR-008 策略）MUST 保证：合法请求正常转发，非法请求返回 OpenAI 风格 401，不泄露内部实现细节

### Key Entities *(include if feature involves data)*

- **渠道（Channel）**: 一个上游 LLM 服务商接入点，含名称、类型（provider）、服务地址、密钥（加密存储）、权重、健康状态。W1 仅使用 DeepSeek 一个渠道
- **模型（Model）**: 渠道下可调用的模型，含模型名、单价（W3 计费用）、启用状态。W1 需要其"可用模型列表"语义
- **用量记录（UsageRecord）**: 一次请求的完整用量事实（请求 ID、Key、渠道、模型、token 数、延迟、状态码、成本）。W1 产生数据雏形，W3 补全计费语义

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 以 OpenAI 官方 JS/TS SDK 仅修改 `base_url`（与 API Key）发起验收，非流式与流式对话均一次成功，SDK 侧零报错——协议兼容性唯一标准
- **SC-002**: 流式对话结束后，usage 事件中 `prompt_tokens` 与 `completion_tokens` 均为非零正整数
- **SC-003**: 一键启动本地环境到交互式 API 文档可访问，新克隆仓库的操作步骤不超过 2 条命令
- **SC-004**: 上游错误场景（不可用 / 鉴权失败）100% 返回 OpenAI 协议错误格式，手工注入 5 类异常请求无一返回内部堆栈
- **SC-005**: 流式首字节到达调用方的时间与上游直接调用相比无可感知劣化（不缓冲整段响应）

## Assumptions

- 调用方持有并使用 OpenAI 官方 SDK（或其他 OpenAI 兼容客户端），本项目不提供面向终端用户的聊天界面（管理台 Playground 属 W4）
- W1 仅接入 DeepSeek 单渠道；通义/智谱渠道与路由/熔断逻辑属 W4 范围
- 多租户、API Key 体系、令牌桶限流、幂等、计费全部不在 W1 范围（分别属 W2/W3），W1 鉴权以环境变量固定管理 Key 过渡
- 开发者本地已具备 Docker 环境；渠道 API Key 由开发者自行准备（见 PROJECT_CONTEXT 第 9 节）
- 验收使用 OpenAI 官方 JS/TS SDK（开发者主力语言，且与后端语言解耦、验证更客观）；openai-python 冒烟为加分项
