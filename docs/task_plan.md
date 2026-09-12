# OneHub 任务计划（W1–W4）

> 本文件是项目唯一进度真相源。只按实际进度更新，不虚构完成状态（禁改清单）。
> 每次会话开始先读本文件同步进度，再动手（AGENTS.md 会话启动规则）。

## 目标

交付 OpenAI 协议兼容的多模型 LLM API 聚合网关：鉴权、限流、计费、高可用四类工程问题各就各位，Locust 压测数字进简历。

## 当前状态

**W1 · Phase 1（T001–T004）+ Phase 2（T005–T009）完成**：Asize 已确认 plan/tasks（停机点 2 解除）。Python 3.12 项目初始化（uv.lock 入库）、FastAPI 骨架（应用工厂 + lifespan + config）、compose（api/pg/redis）三容器构建健康、pytest 基建就绪。Phase 2：7 表 SQLAlchemy 模型 + Alembic async 迁移（001_initial 建齐 7 表 + idx_usage_key_time）+ 种子脚本（deepseek-main + 2 模型，幂等）+ OpenAI 统一错误出口 + Bearer 鉴权（四分支单测）。Checkpoint 证据：`alembic upgrade head` 成功、种子连跑两遍唯一、`uv run pytest -q` → 10 passed、api 容器重建后 /docs 200。**下一步 Phase 3（T010–T014）**：US1 非流式对话 MVP（schemas → Provider 基类 → DeepSeek 实现 → 网关端点 + 错误映射单测）。

## 阶段总览

### W1 协议兼容层 + 单渠道转发（2026-09-10 起）

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 0 | spec-kit 流程 + AGENTS.md + 三件套 | completed | spec/plan/tasks 齐 + 本三件套就位 |
| 1 | 脚手架：FastAPI + SQLAlchemy + Alembic + compose（pg/redis） | completed | `docker compose up` 后 /docs 可访问 ✅；Alembic 接入属 T006（Phase 2）✅ |
| 2 | OpenAI 兼容端点 + DeepSeek Provider（两版接口候选对比后定稿） | in_progress | OpenAI SDK 改 base_url，流式/非流式均能对话 |
| 3 | SSE 流式透传 + delta 累计 usage | pending | 流式结束 usage 事件有 token 数 |
| 4 | teach 检查点：FastAPI async、Pydantic/OpenAPI、适配器模式、httpx、SSE | pending | 讲解通过 |

### W2 多租户鉴权 + 限流

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | 租户-用户-Key 三级模型 + 管理端 JWT；SK- Key 生成/哈希/白名单 | pending | 模型与 CRUD 可用 |
| 2 | 鉴权中间件（哈希校验→状态/白名单/过期） | pending | 全分支单测 |
| 3 | 令牌桶（Lua）+ Semaphore（dev-tdd 先写测试） | pending | 超限 429+Retry-After，并发无竞态 |
| 4 | 幂等键支持 | pending | 重复请求不重复转发 |
| 5 | teach 检查点：API Key 体系、限流四算法、Redis 原子性/Lua、幂等 | pending | 讲解通过 |
| 6 | 📌 简历上墙点 1 + security-best-practices 审查 | pending | 简历初版 + 审查报告 |

### W3 计费引擎

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | models 定价表 CRUD；余额预检（Redis） | pending | 预检不查库、不足返 402 |
| 2 | 用量事件 → Redis Stream → Celery 异步落库 | pending | 链路通畅 |
| 3 | 并发扣减：乐观锁 + 失败重试 | pending | 对账脚本通过（PROJECT_CONTEXT 6.3 标准） |
| 4 | teach 检查点：幂等、事务隔离、并发扣减三方案、削峰 | pending | 讲解通过 |

### W4 智能路由 + 管理台 + 压测

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | 加权轮询 + 指数退避重试（含抖动）；三态熔断器 | pending | 封主渠道自动切备、恢复切回 |
| 2 | React 管理台 + ECharts 仪表盘 + Playground | pending | dogfood 通过 |
| 3 | Locust 压测：200 并发流式 5min | pending | 错误率 <1%，记录 P95 |
| 4 | teach 检查点：重试、熔断、负载均衡、CI/部署 | pending | 讲解通过 |
| 5 | 📌 简历上墙点 2（压测数字入 R 部分） | pending | 简历完整版 |

## 关键决策记录

| 日期 | 决策 | 依据/ADR |
|------|------|---------|
| 2026-09-10 | 采用 spec-kit 流程管理 feature（001-openai-compat-gateway = W1） | PROJECT_CONTEXT 任务 0 |
| 2026-09-10 | constitution 以 PROJECT_CONTEXT 第 2/8 节为源填充 | 治理原则需可执行 |
| 2026-09-11 | /clarify 答案：鉴权 = .env 固定 Key+Bearer（Q1:B）；流式 usage = 无条件注入 include_usage、无 usage 返 0、估算 W3（Q2:A）；models 来源 = DB+种子（Q3:B）；建表 = 全量 7 表（Q4:A）；验收 SDK = JS/TS（Q5:B） | Asize 亲自回答 |
| 2026-09-11 | Provider 接口定稿候选 B：模板方法基类（research.md D1） | design-an-interface 两版对比，W1 任务 2 要求 |
| 2026-09-11 | W1 零新增依赖：渠道密钥走 env，`api_key_encrypted` 存占位，加密方案 W4 走 ADR（research.md D2） | 规避停机点 3 触发，最小改动 |
| 2026-09-12 | Asize 确认 plan/tasks，停机点 2 解除；从 T001 开始 Phase 1。构建工具定 uv（本机 0.12.7，uv.lock 入库） | 任务 0 决策 |
| 2026-09-12 | Phase 2 决策三则：① 种子因 DDL 无唯一约束不用 ON CONFLICT，改"查重→插入/同步"实现幂等（规避改 DDL 停机点）；② pytest 增 `pythonpath=["."]` 以便 tests/ 导入 app.*；③ 鉴权不强制 `sk-` 前缀（conftest 的 key 无前缀），"前缀格式"分支 = Authorization scheme 校验 | 执行期工程决策 |

## 遇到的错误

| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| seed ON CONFLICT 报 `no unique or exclusion constraint matching`（channels.name 无唯一约束） | 2 | 改查重后再插入；已有行同步关键字段，事务内完成（不改 DDL，规避停机点 3） |
| alembic/TestClient 读取 UTF-8 中文 ini 报 GBK 解码错 | 2 | alembic.ini 注释改英文（configparser 用 locale 编码读） |
| pytest 找不到 `app` 模块 | 2 | pyproject `[tool.pytest.ini_options] pythonpath=["."]` |
| TestClient 触发 500 时直接抛异常 | 2 | `TestClient(..., raise_server_exceptions=False)` 让 ServerErrorMiddleware 转交统一出口 |
