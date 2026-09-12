# Implementation Plan: OpenAI 协议兼容网关（W1）

**Branch**: `001-openai-compat-gateway` | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-openai-compat-gateway/spec.md`

## Summary

交付 OpenAI 协议兼容层 + DeepSeek 单渠道转发：`/v1/chat/completions`（流式/非流式）与 `/v1/models` 两个端点，OpenAI 官方 JS/TS SDK 只改 `base_url` 即可正常对话（唯一硬标准）。技术路径：FastAPI + httpx 流式转发 + 手写 SSE 解析（保护清单，TDD）；全量 7 表 Alembic 迁移一次建齐，渠道/模型走 DB + 种子数据，上游密钥走环境变量（FR-006）；鉴权用 `.env` 固定管理 Key + Bearer 校验过渡（W2 替换为 Key 表）。

## Technical Context

**Language/Version**: Python 3.12（验收脚本另用 Node 20 + openai JS SDK）

**Primary Dependencies**: FastAPI、Pydantic v2、pydantic-settings、SQLAlchemy 2.0 (async)、Alembic、httpx、asyncpg、uvicorn——与技术栈清单严格一致，**零新增依赖**

**Storage**: PostgreSQL 16（全量 7 表 + 种子数据）；Redis 7（W1 仅容器就位，无业务读写——限流 W2、余额缓存 W3）

**Testing**: pytest（SSE 解析/usage 累计属保护清单，dev-tdd 先写测试）；端到端验收 = OpenAI 官方 JS/TS SDK 脚本

**Target Platform**: Linux 容器（Docker Compose：api + pg + redis 三服务，worker W3 加、nginx W4 加）

**Project Type**: web-service（LLM API 网关）

**Performance Goals**: W1 无硬性能指标（压测 W4）；流式首字节无可感知劣化（SC-005：不缓冲整段响应）

**Constraints**: OpenAI SDK 零改动兼容（唯一硬标准）；不引入技术栈清单外依赖（触发即停机点 3）

**Scale/Scope**: 单渠道（DeepSeek）、7 端点中的 2 个网关面端点、全量表结构

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 原则 | 状态 | 说明 |
|---|------|------|------|
| I | 协议兼容性唯一硬标准 | ✅ | 计划以 JS SDK 只改 base_url 为核心验收（SC-001），错误映射保协议格式 |
| II | 最小改动不过度工程 | ✅ | 零新增依赖；W1 不写 Redis 客户端/管理面/frontend；渠道密钥走 env 不提前做加密存储 |
| III | 核心算法 TDD + 保护清单手写 | ✅ | SSE 透传与 usage 解析手写实现，任务顺序强制测试先行（见 tasks.md） |
| IV | 证据先行 | ✅ | quickstart.md 定义可复现验收命令，禁止口头完成 |
| V | 停机点 | ✅ | /clarify 已过（5 问已答）；本计划 + tasks 产出后停机等 Asize 确认才写代码（停机点 2） |

Phase 1 设计后复查：无违例，无需 Complexity Tracking 条目。

## Project Structure

### Documentation (this feature)

```text
specs/001-openai-compat-gateway/
├── plan.md              # 本文件（/speckit-plan 输出）
├── research.md          # Phase 0：技术决策与候选对比
├── data-model.md        # Phase 1：7 表数据模型（W1 使用范围标注）
├── quickstart.md        # Phase 1：端到端验收指南
├── contracts/           # Phase 1：OpenAI 兼容 API 契约
│   └── openai-compat-api.md
├── checklists/
│   └── requirements.md  # spec 质量清单（16/16 通过）
└── tasks.md             # /speckit-tasks 输出（本命令不创建）
```

### Source Code (repository root)

```text
onehub/
├── AGENTS.md               # 已生成
├── docker-compose.yml      # W1: api + pg + redis（worker W3、nginx W4 增补）
├── .env.example            # DATABASE_URL/REDIS_URL/SECRET_KEY/GATEWAY_API_KEY/DEEPSEEK_API_KEY
├── backend/
│   ├── pyproject.toml      # uv/pip 管理，锁文件入 git
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/       # 001_initial：全量 7 表（只增不改）
│   ├── app/
│   │   ├── main.py         # FastAPI 工厂 + lifespan（DB 引擎生命周期）
│   │   ├── core/
│   │   │   ├── config.py   # pydantic-settings
│   │   │   └── security.py  # Bearer 校验（constant-time 比对）
│   │   ├── models/         # SQLAlchemy：tenants/users/api_keys/channels/models/usage_records/balances
│   │   ├── schemas/         # Pydantic：OpenAI 协议模型（chat.py）
│   │   ├── api/
│   │   │   └── v1/
│   │   │       └── gateway.py   # POST /v1/chat/completions、GET /v1/models
│   │   ├── services/
│   │   │   └── forward.py  # SSE 透传 + usage 解析（保护清单，TDD）
│   │   └── providers/
│   │       ├── base.py      # BaseProvider（模板方法基类，候选 B 定稿）
│   │       └── deepseek.py  # DeepSeekProvider（差异点：headers/base_url）
│   └── tests/
│       ├── unit/            # sse 解析、usage 累计、鉴权、错误映射
│       └── conftest.py
├── scripts/
│   ├── seed.py             # channels(1 行 deepseek) + models(deepseek-chat 等) 种子
│   └── acceptance/          # JS/TS SDK 验收（独立 package.json，不混入 backend）
│       ├── package.json
│       └── verify.mjs       # 非流式/流式/usage/401/models 五项断言
└── docs/                    # 已有三件套 + PROJECT_CONTEXT.md
```

**Structure Decision**: 按 PROJECT_CONTEXT 第 4 节强制目录生成；W1 实际创建范围 = `backend/` + `docker-compose.yml` + `scripts/`（种子与 JS 验收脚本不属于 backend Python 包，独立成目录——对强制结构的唯一增补，叶子级，不属停机点 3 的"改目录结构"）。`frontend/`（W4）、`app/workers/`（W3）、`app/api/admin/`（W2）暂不创建空目录，到周创建。

## Complexity Tracking

> 无 Constitution 违例需辩护。渠道密钥 W1 不做加密存储（`api_key_encrypted` 字段以显式占位符过 NOT NULL，运行时密钥从 env 注入，符合 FR-006）：加密方案选型涉及新增依赖，按停机点 3 规则推迟到 W4 管理台渠道 CRUD 时以 ADR 提案。
