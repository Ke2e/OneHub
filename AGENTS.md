# AGENTS.md — OneHub

OneHub：OpenAI 协议兼容的多模型 LLM API 聚合网关。开发者 Asize 的简历核心项目，对标后端 / AI Infra / LLM Gateway 实习岗——技术选型的第一判据是"面试时能不能讲出工程问题"。

## 会话启动

1. 读 `docs/task_plan.md` 同步当前进度（三件套之一，见下）
2. 有活跃 feature 时，读 `specs/` 下对应 `spec.md` 与 `checklists/requirements.md`

需要细节时的指针：

- **技术栈、目录结构、DDL、核心算法规格、W1–W4 周计划**：读 `docs/PROJECT_CONTEXT.md`（唯一事实来源，本文件不重复其内容）
- **治理原则**：读 `.specify/memory/constitution.md`
- **决策记录**：读 `docs/adr/`（架构级变更先提案到这里）

## 保护清单（简历卖点，手写实现）

令牌桶、三态熔断器、计费幂等与乐观锁扣减、SSE 流式透传与 usage 解析——按 `docs/PROJECT_CONTEXT.md` 第 6 节规格手写。换成熟库（redis-cell / pybreaker 等）等于破坏简历叙事，一律拒绝。这五类组件豁免 ponytail / security 审查提出的"换库简化"建议。

## 停机点（等 Asize 亲自批准，才能继续）

1. `/speckit-clarify` 的问题：Asize 亲自回答，代答无效
2. 周计划产出后：Asize 确认后才写代码
3. 架构级决策（新增外部依赖 / 改 DDL / 改目录结构）：先写 ADR 提案到 `docs/adr/`，等批准

## 禁改清单

- `alembic/versions/` 已生成的迁移：只增不改
- 真实密钥文件永不入 git，入库的只有 `.env.example`
- `docs/task_plan.md` 只按实际进度更新，不虚构完成状态

## 工作流

- 核心算法（保护清单内）：先写测试再实现
- 声称"完成"必须附命令证据，没有可复现输出的完成不算完成
- 提交遵循 Conventional Commits
- 改动保持外科手术式：只动与当前任务相关的行，不顺手重构

## 进度三件套

`docs/task_plan.md`（计划与进度，唯一进度真相源）、`docs/findings.md`（跨会话技术事实）、`docs/progress.md`（会话日志）——任务状态与发现先落盘，跨会话靠它们续接。另有 `docs/notes.md`（每周学习笔记）与 `docs/adr/`（决策记录）随对应场景建立。
