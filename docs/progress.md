# OneHub 会话日志（progress）

## 会话 2026-09-12（Phase 4：T015–T017 + CP4.1 达成，US2 流式 SSE 透传可演示）

**背景**：Phase 3 完成（US1 非流式 MVP 200），Docker 三容器健康，.env 含真实 SenseAudio 渠道 key。从 T015 进入 US2 流式（保护清单，测试先行）。

**做了什么**：

1. T015（RED）测试先行：`tests/unit/test_sse_parser.py` + `tests/fixtures/sse_stream.txt`——五能力全覆盖：data: 前缀剥离 / 空行分隔 / `[DONE]` 终止（后续行不产出）/ 坏 JSON 事件跳过（前后事件不受影响）/ EOF 截断自然收敛；另含多 data: 行拼接与注释/空白行忽略。运行确认 `ModuleNotFoundError: app.services` FAIL 后单独提交 `d77cb29`
2. T016 实现转 GREEN：`app/services/forward.py` 手写 SSE 解析器（保护清单，禁换库）——`SSELineParser` 同步状态机（feed 增量喂行、空行结算事件、`[DONE]` done、坏 JSON 跳过、finish() EOF 兜底）+ `sse_events()` 异步适配（httpx aiter_lines 直喂）；`base.py` chat_stream() 实现：`client.stream()` + 状态码检查（复用 `_map_upstream_error`）+ 逐事件产出，超时/连接失败映射 504/502。5 passed，全量 22 passed，提交 `ec094bd`
3. T017 端点：`gateway.py` 增 `stream: true` 分支——`_stream_events()` 事件 dict → `data: {json}\n\n` + `data: [DONE]` 收尾，`StreamingResponse(media_type="text/event-stream")` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`；调用方断连由生成器取消传播终止上游（async with 退出关闭连接）。提交 `6307935`
4. 验收（dev-verify 证据）：临时 httpx 脚本模拟 SDK 改 base_url 打本机网关 → 上游真实渠道

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **22 passed**（17 存量 + 5 新增 SSE 解析器）
- 真 key 流式转发（本机 uvicorn :8001，DATABASE_URL 覆盖 localhost）→ `HTTP 200 text/event-stream`、**12 个 data 事件逐块透传**、delta 拼接完整 = "我是 CP4-OK，一个专注高效解决问题的智能助手，随时准备为你提供简洁、准确的帮助。"、`data: [DONE]` 正常收尾 ✅
- `git log` TDD 时间序：`d77cb29`(test RED) → `ec094bd`(feat GREEN) → `6307935`(端点)

**下一步**：Phase 5 T018–T020（US3 流式 usage：末尾 chunk 携带 token 数，include_usage 注入已在 `_build_payload` 就位，T018 先写 usage 提取测试先 RED）。

## 会话 2026-09-12（Phase 3：T010–T014 + CP3.1 达成，US1 非流式 MVP 可演示）

**背景**：Phase 3 前置就绪（config 双键名 + env_file 绝对路径，上一会话）。Docker 三容器运行中，.env 含真实渠道 key。

**做了什么**：

1. T010 schemas：`app/schemas/chat.py`（ChatCompletionRequest 含透传 + extra=allow、Message、ChatCompletionResponse/Choice/Usage、流式 ChatCompletionChunk/Delta/ChunkChoice）严格对齐 contracts
2. T011+T012 providers：`base.py` BaseProvider ABC（`_headers()` 抽象；chat() 通用实现——AsyncClient 连接池（limits 100/20）+ `_normalize_base_url()` 自动补 /v1、`_build_payload()`（exclude_none + 非流式不注入）、五类错误映射、响应结构校验零堆栈；chat_stream 占位留 Phase 4）+ `deepseek.py`（仅 Bearer 头差异点）
3. T013 gateway：`app/api/v1/gateway.py` POST /v1/chat/completions（require_gateway_api_key → models 表 enabled 校验 404 model_not_found → app.state 单例 provider → provider.chat()）；main.py lifespan 构造 provider 单例 + router 注册
4. T014 单测：`tests/unit/test_error_mapping.py`（401→authentication_error / 429→rate_limit_error / 502→api_error / 超时→504 / 连接拒绝→502 + 200 结构非法兜底 + 2xx 回归对照；MockTransport 注入，message 防泄漏断言）
5. Asize 决策落地：base_url 自动规整 /v1 + 种子模型更新（deepseek-chat/reasoner → deepseek-v4-flash-0731/senseaudio-s2，SenseAudio 平台实际可用），dev 库数据同步

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **17 passed**（10 存量 + 7 新增错误映射）
- 本机 uvicorn（localhost:8001，DATABASE_URL 覆盖 localhost）三分支：401 authentication_error ✅ / 404 model_not_found ✅ / 真 key 非流式转发 **200**（object=chat.completion，content=CP31-OK，usage 12/5/17，finish=stop）✅
- 真机诊断链：getaddrinfo 失败（.env 主机名 pg）→ 502（上游 404：中转只认 /v1）→ 400 模型未找到 → 中转 /v1/models 列模型 → 决策落地后 200

**下一步**：Phase 4 T015–T017（US2 流式 SSE 透传，保护清单 T015 先写测试先 RED）。

## 会话 2026-09-12（Phase 2：T005–T009 + Checkpoint 达成，阻塞解除）

**背景**：Docker 三容器运行中，.env 已建，从 T005 继续 Phase 2（不依赖真实 key）。

**做了什么**：

1. T005 SQLAlchemy 7 表：`app/models/`（tenant/user/api_key/channel/model/usage_record/balance + `__init__.py` 定义 Base 聚合导出），严格按 PROJECT_CONTEXT 第 5 节 DDL（BIGSERIAL/CHAR(64) key_hash/ARRAY(Text) whitelist/UUID request_id 唯一 + idx_usage_key_time 复合索引）
2. T006 Alembic：`alembic.ini` + `alembic/env.py`（async 引擎，URL 由 Settings 注入：环境变量优先、本地回退 localhost）+ autogenerate 生成 `001_initial`（7 表 + 索引一次建齐，只增不改）→ `alembic upgrade head` 对 pg 执行成功
3. T007 种子：`scripts/seed.py`（deepseek-main 渠道 `api_key_encrypted=env-injected` + deepseek-chat/deepseek-reasoner 两模型挂渠道；查重式幂等 upsert，sys.path 注入 backend）
4. T008 错误出口：`app/core/errors.py`（OpenAI 错误结构 `{"error":{message,type,param,code}}`；OpenAIError/AuthenticationError/ModelNotFoundError/RateLimitError/UpstreamError；422→invalid_request_error、未捕获→api_error 零堆栈、401 带 WWW-Authenticate）→ 注册进 create_app
5. T009 鉴权：`app/core/security.py`（extract_bearer_token + hmac.compare_digest constant-time 比对）+ `tests/unit/test_security.py` 四分支（有效/缺失/值错误/scheme 格式错）
6. 补错误出口冒烟单测 `tests/unit/test_errors.py`（422/404 model_not_found/500 三类出口结构断言）

**验证（dev-verify 证据）**：

- `alembic upgrade head` → `Running upgrade -> d36075ae03bd`；pg `\dt` 7 表 + `idx_usage_key_time` 全在
- `uv run python ../scripts/seed.py` 连跑两遍 → channels 1 行 / models 2 行唯一，外键挂接正确
- `uv run pytest -q` → **10 passed**（冒烟 1 + 鉴权 6 + 错误出口 3）
- `uv run python -c "from app.main import app"` → OpenAIError/RequestValidationError/Exception handlers 已注册
- `docker compose up -d --build api` 重建 → `GET /docs` → **200**

**下一步**：Phase 3 T010–T014（US1 非流式 MVP：schemas → Provider 模板方法基类 → DeepSeek 实现 → POST /v1/chat/completions + 错误映射单测）。Phase 3 起需真实 `DEEPSEEK_API_KEY`（.env 填入）。

## 会话 2026-09-12（Phase 1：T001–T004 + Checkpoint 达成，停机点 2 解除）

**背景**：Asize 确认 plan/tasks，从 T001 开始执行 Phase 1。

**做了什么**：

1. T001 pyproject.toml：fastapi/uvicorn/sqlalchemy[asyncio]/alembic/asyncpg/httpx/pydantic-settings + pytest/pytest-asyncio dev 组；uv lock 生成 uv.lock（37 包）入库；uv sync 落 backend/.venv（Python 3.12.3）
2. T002 FastAPI 骨架：`app/main.py`（create_app 应用工厂 + lifespan 管理 AsyncEngine 池，pool_pre_ping）+ `app/core/config.py`（pydantic-settings 五变量，默认值与 .env.example 对齐）
3. T003 容器编排：`docker-compose.yml`（api/pg/redis 三服务 + healthcheck + 依赖等待）+ `backend/Dockerfile`（python:3.12-slim + uv 0.12.7 二进制，uv sync --frozen --no-dev 层缓存）+ `.env.example`（五变量含注释，密钥红线不入 git）
4. T004 pytest 基建：`tests/conftest.py`（env 覆盖 + session event loop）+ 冒烟测试；pyproject 增 `asyncio_default_fixture_loop_scope`
5. Checkpoint：起 Docker Desktop → 创建 .env（复制模板）→ `docker compose up -d --build` → 探活

**验证（dev-verify 证据）**：

- `uv run pytest -q` → `1 passed in 0.07s`
- `uv run python -c "from app.main import app"` → 路由含 `/docs` `/openapi.json`
- `docker compose up -d --build` → 三服务 Up，pg/redis healthy
- `Invoke-WebRequest http://localhost:8000/docs` → **HTTP 200，`OneHub Gateway - Swagger UI`**
- 提交链：`a66bd06`（T001）→ `0bcc421`（T002）→ `7c496bc`（T003）→ `772a439`（T004），各自独立 Conventional Commits

**下一步**：Phase 2 T005–T009（7 表 SQLAlchemy + Alembic 全量迁移 + seed 幂等 + OpenAI 错误出口 + Bearer 鉴权单测），Checkpoint = 迁移可对 pg 执行、种子幂等、错误与鉴权单测绿。

## 会话 2026-09-12（git 仓库初始化，跨会话前保障）

**做了什么**：

1. 根 `.gitignore`：挡 `.env`（密钥红线）、Python（`__pycache__`/`.venv`/`.pytest_cache`）、Node（`node_modules`）、Docker 本地数据卷（`postgres_data/` 等）、IDE/OS 杂物；显式注明 uv.lock 与 alembic 迁移必须入库
2. `git init`（主分支 main）+ 全量暂存 + 首次提交
3. `.specify/feature.json` 被 spec-kit 自带 `.gitignore` 正确忽略（本地指针，按其约定不入库）

**验证（dev-verify 证据）**：

- `git log --oneline` → `fd694d9 chore: 初始化仓库——spec-kit 脚手架、W1 规格五件套、AGENTS.md 与进度三件套`（44 文件，5933 行）
- `git status` → `nothing to commit, working tree clean`
- 暂存清单人工核查：零 `.env` 类文件命中（密钥红线守卫成立）

**意义**：TDD 证据链就位——后续 T015/T018（保护清单测试先行）可通过 git log 时间序自证。

## 会话 2026-09-11（W1 任务 0 收尾：clarify 回填 + plan + tasks）

**做了什么**：

1. Asize 确认全部按推荐回答 /clarify 五问（Q1:B / Q2:A / Q3:B / Q4:A / Q5:B）
2. 回填 spec：全部 [NEEDS CLARIFICATION] 清除，增 Clarifications 节（5 条 Q→A 记录）；checklist 16/16 通过
3. /speckit-plan 产出五件套（specs/001-openai-compat-gateway/）：
   - plan.md（技术上下文/Constitution Check 5 项全过/W1 源码结构）
   - research.md（D1 Provider 接口两版对比→候选 B 模板方法基类；D2 密钥 env 注入+占位；D3 SSE 要点；D4 错误映射；D5 compose 分期）
   - data-model.md（7 表全量、W1 只消费 channels/models，种子规格）
   - contracts/openai-compat-api.md（两端点 + 错误契约 + include_usage 注入规则）
   - quickstart.md（SC-001~005 验收手册，≤2 命令起环境 + JS SDK 五断言）
4. /speckit-tasks 产出 tasks.md：T001–T027，八阶段（Setup → Foundational → US1 非流式 → US2 SSE[TDD] → US3 usage[TDD] → US4 models → US5 环境 → Polish 验收）；保护清单 T015/T018 强制测试先行
5. 三件套同步：决策记录 +3 条，任务 0 状态更新

**测试/验证**：

- 无代码。质量证据：spec checklist 16/16；Constitution Check 五原则全过（plan.md）；任务格式全部含 checkbox/ID/文件路径

**停机点 2（当前）**：plan/tasks 已产出，**等 Asize 确认后才写代码**。确认后从 T001（pyproject 初始化）开始执行 Phase 1。

**下一步（确认后）**：

1. Phase 1 T001–T004：骨架 + compose + pytest 基建 → checkpoint（/docs 200）
2. Phase 2 T005–T009：7 表 + 迁移 + 种子 + 错误出口 + 鉴权
3. 后续按 tasks.md 依赖序推进，MVP = US1（非流式）


## 会话 2026-09-10（W1 任务 0，spec-kit 启动）

**做了什么**：

1. `specify init . --integration claude`（v1.0.1，ps 脚本）→ `.specify/` + `.claude/skills/speckit-*` 就位
2. 填充 `.specify/memory/constitution.md`：五条核心原则（协议兼容硬标准 / 最小改动 / TDD+保护清单 / 证据先行 / 停机点），源自 PROJECT_CONTEXT 第 2/8 节
3. /speckit-specify 产出 `specs/001-openai-compat-gateway/spec.md`：5 个 User Story（P1×3：非流式/流式/usage；P2×2：models/错误映射；P3：一键环境）+ FR-001~011 + 成功标准 + 4 个 [NEEDS CLARIFICATION]
4. 质量清单 `specs/001-openai-compat-gateway/checklists/requirements.md`：除"NEEDS CLARIFICATION 清零"外全部通过
5. `.specify/feature.json` 写入活跃 feature 目录
6. AGENTS.md 生成（writing-for-agents 原则：不复制 PROJECT_CONTEXT，只做指针 + 保护清单/停机点/禁改清单/工作流）
7. 三件套建立：docs/task_plan.md（W1–W4 全计划）、docs/findings.md、docs/progress.md（本文件）
8. /speckit-clarify 扫描完成，5 问已列给 Asize——**停机等待亲自回答**

**测试/验证**：

- 无代码，无测试。spec 自检：checklists/requirements.md 15/16 通过，唯一未过项 = NEEDS CLARIFICATION 未清零（预期内，等回答）

**下一步**（等 Asize 回答 Q1–Q5 后）：

1. 回填 spec（去 NEEDS CLARIFICATION 标记）+ 更新 checklist
2. /speckit-plan（技术方案）→ /speckit-tasks（任务清单）
3. 进入 W1 任务 1：脚手架（compose 起 pg/redis + FastAPI，验收 /docs 可访问）

**未决事项**：

- /clarify Q1–Q5 等 Asize 亲自回答（停机点 1，禁止代答）
- notes.md（周记）与 docs/adr/ 在首个使用场景出现时创建
