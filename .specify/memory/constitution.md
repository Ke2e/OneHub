# OneHub Constitution

> 项目最高治理文件。与 `docs/PROJECT_CONTEXT.md` 冲突时，以 PROJECT_CONTEXT.md 为准并回头修订本文件。

## Core Principles

### I. 协议兼容性是唯一硬标准（NON-NEGOTIABLE）

OpenAI 官方 SDK 只改 `base_url` 指向本网关，流式/非流式均正常对话。任何改动若破坏此标准，一律视为回归，无论其他指标多好看。

### II. 最小改动，不过度工程

遵守 karpathy-guidelines：外科手术式修改、不投机抽象、不为假设性未来设计。三行相似代码优于过早抽象。ponytail-review 负责剪掉意外复杂度（保护清单豁免）。

### III. 核心算法 TDD + 保护清单手写（NON-NEGOTIABLE）

令牌桶、熔断器、计费幂等/乐观锁、路由、SSE 流式透传与 usage 解析——保护清单内组件：

- dev-tdd 先写测试再实现
- 禁止替换为成熟库（不用 redis-cell / pybreaker 等）
- security / ponytail 审查豁免（因为它们本来就该"重"）

### IV. 证据先行，禁止口头"完成"

完工必须 dev-verify 拿到命令级证据 + webapp-testing 自动验收。没有可复现命令输出的"完成"不算完成。

### V. 尊重停机点

以下场景必须停下等人工，禁止自动继续：

1. spec-kit /clarify 的问题——开发者亲自回答，禁止代答
2. 周计划产出后——开发者确认
3. 架构级决策（新增外部依赖 / 改 DDL / 改目录结构）——先记 ADR 提案，等人批

## Additional Constraints

- **技术栈锁定**（见 PROJECT_CONTEXT 第 2 节）：不引入清单外重型依赖；任何新增依赖都是停机点
- **DDL 以 PROJECT_CONTEXT 第 5 节为准**：`alembic/versions/` 已生成迁移只增不改
- **密钥安全**：`.env` / `.env.example` 之外的真实密钥文件永不入 git
- **进度诚实**：`docs/task_plan.md` 只按实际进度更新，不虚构完成状态

## Development Workflow

1. 每次会话开始：先读 `AGENTS.md` 与 `docs/task_plan.md`，同步进度后再动手
2. 周初：dev-grill-docs / dev-plan 出周计划，等开发者确认后才写代码
3. spec-kit 主线：/speckit-specify → /speckit-clarify（人工回答）→ /speckit-plan → /speckit-tasks → /speckit-implement
4. 完工流程：dev-verify → dev-code-review → ponytail-review（保护清单豁免）→ dev-commit-writer（Conventional Commits）

## Governance

- 所有规格、计划、代码审查必须验证是否符合本 Constitution
- 复杂度必须被证明合理，否则删除
- 修订本文件需开发者批准，并保留修订记录

**Version**: 1.0.0 | **Ratified**: 2026-09-10 | **Last Amended**: 2026-09-10
