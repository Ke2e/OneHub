# Tasks: OpenAI 协议兼容网关（W1）

**Input**: Design documents from `specs/001-openai-compat-gateway/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/openai-compat-api.md ✅, quickstart.md ✅

**Tests**: 已明确要求——Constitution III 规定保护清单组件（SSE 透传、usage 解析）强制 dev-tdd 测试先行；鉴权与错误映射单测为 SC-004 验收证据。

**Organization**: 按用户故事分组。路径约定：后端 `backend/app/`、测试 `backend/tests/`、种子与验收 `scripts/`（见 plan.md Structure Decision）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: US1–US5 对应 spec.md 用户故事

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与最小可运行骨架

- [ ] T001 初始化 Python 3.12 项目：`backend/pyproject.toml`（fastapi/uvicorn/sqlalchemy/alembic/asyncpg/httpx/pydantic-settings + pytest/pytest-asyncio dev 组，版本锁定，锁文件入 git）
- [ ] T002 [P] FastAPI 骨架：`backend/app/main.py`（应用工厂，lifespan 管理 async 引擎，`/docs` 开启）+ `backend/app/core/config.py`（pydantic-settings 读 `.env`：DATABASE_URL/REDIS_URL/SECRET_KEY/GATEWAY_API_KEY/DEEPSEEK_API_KEY）
- [ ] T003 [P] 容器编排：`docker-compose.yml`（api/pg/redis 三服务，W1 不建 worker/nginx）+ `backend/Dockerfile` + `.env.example`（五变量含注释）
- [ ] T004 [P] pytest 基建：`backend/tests/conftest.py`（event loop fixture + 配置覆盖）

**Checkpoint**: `docker compose up -d --build` 后 `curl localhost:8000/docs` 返回 200

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事的前置件。⚠️ 完成本阶段前不得开始任何故事

- [ ] T005 SQLAlchemy 7 表定义：`backend/app/models/`（tenant.py/user.py/api_key.py/channel.py/model.py/usage_record.py/balance.py + `__init__.py` 聚合），严格按 PROJECT_CONTEXT 第 5 节 DDL 字段与索引
- [ ] T006 Alembic 接入与全量迁移：`backend/alembic/env.py`（async 引擎）+ `alembic/versions/001_initial_*.py`（7 表 + `idx_usage_key_time` 一次建齐，只增不改）
- [ ] T007 种子脚本：`scripts/seed.py`（channels 1 行 deepseek-main，`api_key_encrypted` 占位 `env-injected`；models 2 行 deepseek-chat/deepseek-reasoner；幂等 upsert）
- [ ] T008 统一错误出口：`backend/app/core/errors.py`（OpenAI 错误结构 + 全局 exception handler，422/500/自定义异常一律重塑为 `{"error": {...}}`，注册进 `app/main.py`）——contracts 错误节实现
- [ ] T009 Bearer 鉴权：`backend/app/core/security.py`（constant-time 比对 `GATEWAY_API_KEY`，失败抛 401 OpenAI 结构）+ `backend/tests/unit/test_security.py`（有效/缺失/错误/前缀格式四分支）

**Checkpoint**: 迁移可对 pg 执行；种子幂等；错误与鉴权单测绿

---

## Phase 3: User Story 1 - 非流式对话 (Priority: P1) 🎯 MVP

**Goal**: OpenAI SDK 改 base_url 非流式对话成功（US1 全部验收场景）

**Independent Test**: JS SDK 非流式调用返回 `choices[0].message.content` 非空且 `usage.prompt_tokens > 0`

### Implementation for User Story 1

- [ ] T010 [US1] OpenAI 协议 Pydantic 模型：`backend/app/schemas/chat.py`（ChatCompletionRequest 含透传字段、ChatCompletionResponse、Message/Choice/Usage、流式 Chunk 模型）——严格对齐 contracts/openai-compat-api.md
- [ ] T011 [US1] Provider 模板方法基类：`backend/app/providers/base.py`（BaseProvider ABC：`_headers()` 抽象点 + `chat()`/`chat_stream()` 通用实现，httpx.AsyncClient 连接池、上游错误→OpenAI 结构映射）——按 research.md D1 候选 B 定稿
- [ ] T012 [US1] DeepSeek 实现：`backend/app/providers/deepseek.py`（DeepSeekProvider：base_url `https://api.deepseek.com` + Bearer 头，密钥从 `DEEPSEEK_API_KEY` 注入）
- [ ] T013 [US1] 非流式端点：`backend/app/api/v1/gateway.py`（POST /v1/chat/completions：鉴权依赖 → model ∈ models 表 enabled 集合校验（404 model_not_found）→ 渠道实例化 → `provider.chat()` 透传响应）
- [ ] T014 [P] [US1] 错误映射单测：`backend/tests/unit/test_error_mapping.py`（上游 401/429/502/超时/连接拒绝五类 → OpenAI 结构断言，SC-004 证据）

**Checkpoint**: US1 独立可验——SDK 非流式对话 + 异常请求零堆栈

---

## Phase 4: User Story 2 - 流式 SSE 透传 (Priority: P1)

**Goal**: `stream: true` 逐块透传，`[DONE]` 正常收尾（US2 验收场景）

**Independent Test**: SDK 流式调用逐块收 delta、拼接完整、正常结束

### Tests for User Story 2 ⚠️ 保护清单，先写测试先 RED

- [ ] T015 [US2] **先写** SSE 解析器测试：`backend/tests/unit/test_sse_parser.py` + fixture `backend/tests/fixtures/sse_stream.txt`（data: 前缀剥离、空行分隔、`[DONE]` 终止、坏 JSON 行容错、上游中断流截断）——运行确认 FAIL

### Implementation for User Story 2

- [ ] T016 [US2] SSE 解析与透传：`backend/app/services/forward.py`（httpx `stream()` + `aiter_lines()` 逐块产出、`StreamingResponse` text/event-stream 直通、调用方断开检测终止上游、上游超时）——至测试 GREEN
- [ ] T017 [US2] 流式接入端点：`backend/app/api/v1/gateway.py` 增 `stream: true` 分支（`provider.chat_stream()` → StreamingResponse）

**Checkpoint**: US1+US2 均独立可用；T015 测试转 GREEN

---

## Phase 5: User Story 3 - 流式 usage (Priority: P1)

**Goal**: 流末尾 chunk 携带非零 usage，调用方零配置（US3 验收场景 + SC-002）

**Independent Test**: 流式对话结束后末尾数据 chunk `usage.prompt_tokens/completion_tokens` 为非零正整数

### Tests for User Story 3 ⚠️ 保护清单，先写测试先 RED

- [ ] T018 [US3] **先写** usage 提取测试：`backend/tests/unit/test_usage_extract.py`（末尾 chunk usage 提取、delta 累计口径、上游未返回 usage → 缺省 0 不报错）——运行确认 FAIL

### Implementation for User Story 3

- [ ] T019 [US3] usage 注入与提取：`backend/app/services/forward.py` 增 usage 提取逻辑；`backend/app/api/v1/gateway.py` 对 `stream: true` 请求无条件注入 `stream_options.include_usage=true`（research.md D3 决议）——至测试 GREEN
- [ ] T020 [US3] 流式 usage 端到端验证：`backend/tests/test_e2e_stream.py`（Mock 上游 SSE 或录制的完整对话流，断言末尾 usage 事件）

**Checkpoint**: SC-002 达成；T018 测试转 GREEN

---

## Phase 6: User Story 4 - 模型列表 (Priority: P2)

**Goal**: `GET /v1/models` 返回 DB 来源的 OpenAI 格式列表（US4 验收场景）

**Independent Test**: GET /v1/models 返回 `object: "list"` 且 `data[]` 含 `deepseek-chat`

- [ ] T021 [P] [US4] models 端点：`backend/app/api/v1/gateway.py` 增 GET /v1/models（查 models 表 enabled 集合 → OpenAI list 格式）
- [ ] T022 [US4] 端点单测：`backend/tests/unit/test_models_endpoint.py`（种子数据 → 列表结构与内容断言）

**Checkpoint**: US4 独立可用（仅依赖 Phase 2）

---

## Phase 7: User Story 5 - 一键环境 (Priority: P3)

**Goal**: 克隆后 ≤2 条命令起全环境，可重复启动（SC-003，US5 验收场景）

**Independent Test**: `docker compose down -v && docker compose up -d --build` 后 /docs 200，再重复 up 无手工清理

- [ ] T023 [US5] 启动自动化：api 容器 entrypoint 串 `alembic upgrade head && python scripts/seed.py`（种子幂等，research.md D5），写入 `backend/Dockerfile` 或 `docker-compose.yml` command
- [ ] T024 [US5] 环境验收：按 quickstart.md 执行 down -v → up → /docs 探活 → 重复 up 验证幂等，命令输出留证（dev-verify 证据规则）

**Checkpoint**: SC-003 达成

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 协议兼容总验收与 W1 收尾

- [ ] T025 [P] JS SDK 验收脚本：`scripts/acceptance/package.json` + `scripts/acceptance/verify.mjs`（五断言：非流式/流式/流式 usage/401 鉴权/models 列表，任一失败 exit 1）
- [ ] T026 全链路验收：跑通 quickstart.md 完整流程（compose 环境 + `npm run verify` 五断言全过 + SC-004 错误抽查），命令级证据入 `docs/progress.md`——SC-001/002/004/005 终判
- [ ] T027 [P] W1 收尾文档：更新 `docs/task_plan.md`（任务 0-4 状态与证据）、`docs/notes.md`（周学习笔记）、teach 检查点材料（FastAPI async / Pydantic/OpenAPI / 适配器模式 / httpx / SSE 转发）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**：无依赖，立即开始
- **Phase 2**：依赖 Phase 1（骨架与 compose 存在）——**阻塞全部故事**
- **Phase 3 (US1)**：依赖 Phase 2；W1 核心 MVP
- **Phase 4 (US2)**：依赖 Phase 3（provider 基类与端点外壳复用）
- **Phase 5 (US3)**：依赖 Phase 4（usage 提取在 SSE 解析器之上）
- **Phase 6 (US4)**：仅依赖 Phase 2，**可与 Phase 3-5 并行**
- **Phase 7 (US5)**：依赖 Phase 1+2 实质（compose/迁移/种子）；验证类任务依赖 Phase 3-6 完成更顺
- **Phase 8**：依赖 Phase 3-7 全部

### Within Each User Story

- 保护清单（T015/T018）：测试先写、先 RED、后实现转 GREEN——Constitution III，不可颠倒
- schemas → provider → endpoint 顺序（依赖方向）
- 每任务或逻辑组提交一次，Conventional Commits

### Parallel Opportunities

- T002/T003/T004 并行（T001 后）
- T014 与 T013 可并行（不同文件）
- Phase 6 (US4) 可整体与 Phase 3-5 并行
- T025 可在 Phase 5 完成后提前写（依赖端点契约而非实现细节）

---

## Implementation Strategy

### MVP First (US1)

1. Phase 1 + Phase 2 → 骨架与数据层就绪
2. Phase 3 (US1) → **STOP 验证**：SDK 非流式对话成功 = 最小可演示里程碑
3. Phase 4→5 (US2→US3) → 流式主链路（W1 验收标准主体）
4. Phase 6/7 → 列表与环境体验补全
5. Phase 8 → 总验收 + 文档收尾

### W1 完成判据

quickstart.md 全流程跑通（T026）——即 PROJECT_CONTEXT W1 三条验收：compose /docs 可访问、SDK 流式/非流式均能对话、流式结束 usage 事件有 token 数。

---

## Notes

- 保护清单组件（SSE 透传 forward.py、usage 解析）：手写、禁换库、ponytail/security 审查豁免（AGENTS.md）
- 任务顺序即提交顺序，git log 须可证 TDD 时间序（T015/T018 测试提交先于对应实现提交）
- `api_key_encrypted` 占位与密钥 env 注入的完整理由见 research.md D2；W4 加密方案走 ADR（停机点 3）
