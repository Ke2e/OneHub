# OneHub · 简历上墙点 1（初版草稿）

> W2 任务 6 产出。基于 W1–W2 已落地 + 已留证成果撰写，数字全部有命令级证据
> （pytest 计数、真 Redis 冒烟、真机验收），不虚构。
> 用途：简历"项目经历"块单条可摘抄内容；面试前对照 docs/notes.md 五讲复习。
> 版本：v1.0（W2 末）。W4 压测数字（P95/错误率）注入后出完整版（上墙点 2）。

---

## 项目一句话

**OneHub — OpenAI 协议兼容的多模型 LLM API 聚合网关**（Python / FastAPI / SQLAlchemy / Redis）
作为独立后端项目，完整经历「鉴权 → 限流 → 计费 → 高可用」网关全链路四个工程问题。

## 技术栈（简历行）

`Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL 16 · Redis 7 (Lua) · Docker Compose · httpx · PyJWT`

## 简历条目（可摘抄块，中文版；英文版见文末迁移提示）

### 上墙点 1：鉴权体系 + 分布式限流 + 幂等键（W1–W2 已达成）

> 岗位方向：后端 / AI Infra / LLM Gateway 实习
> 定位：本项目是简历核心项目，以下三条各注 [手写] = 面试高频深挖点（保护清单）。

**① OpenAI 协议兼容网关链路（对标 LLM 应用层）**
- 搭建 FastAPI 异步网关，非流式 + 流式（SSE 透传）双通道对齐 OpenAI SDK，调用方仅改 `base_url` 即可接入——`openai` SDK 五断言验收脚本 5/5 通过（流式 delta 拼接完整、末尾 usage 非零、错误结构为官方 `{error:{type,code}}` 形态）
- 手写 SSE 解析状态机 + usage 提取（上游未返回 usage 时合成 0 兜底，调用方零配置拿到 token 数）——全部手写实现，不引第三方解析库
- 422/404/401/429/500 统一错误出口，零内部堆栈泄露（防信息泄露）

**② 多租户 API Key 鉴权体系 [手写]**
- 三级模型（租户→用户→Key）+ 管理面 PBKDF2 密码哈希 & JWT HS256；SK-Key 仅存 SHA-256 哈希与前缀，明文创建时一次返回、库里永不可逆——「拖库也拿不到可用 Key」
- 网关面哈希查表 → 状态（软删可吊销）→ 过期时间 三层瀑布校验 + Key 级模型白名单（鉴权与授权解耦）
- 单测全分支覆盖（6 分支鉴权 + 4 分支白名单），真机 7 断言 ALL_PASS

**③ Redis 手写令牌桶限流 + 并发控制 [手写]**
- RPM/TPM 双维度令牌桶，Redis Lua 脚本内 read-modify-write 原子执行——EVAL 单线程执行模型保证多实例共享桶零竞态；28 并发压力冒烟恰 20 放行零超发
- 桶拒/槽满均 429 + `Retry-After` 头（RPM 限额与并发在途上限双通道）
- 进程内并发槽（等价 Semaphore 非阻塞原语），流式请求槽位在生成器 finally 释放（断连不泄漏）

**④ 幂等键防重复转发 [手写]**
- `Idempotency-Key` 头 + Redis 缓存完整非流式响应（TTL 24h）——同 key 重复请求直接回放，不转发上游、不重复计费
- Lua 原子占位（查缓存→查占位→抢占位三步打包）防止并发双转发；得主失败撤销占位可重试，超时 409
- 10 并发同 key 真 Redis 冒烟：恰 1 得主其余 9 回放同一响应

## 量化证据（面试数字弹药，全部可复现）

| 指标 | 值 | 证据 |
|------|-----|------|
| 单测通过数 | **92 passed**（W1 32 → W2 92） | `uv run pytest -q` |
| 真机验收（真实 LLM 渠道） | 非流式 200 + usage、流式 12 事件逐块透传 + [DONE]、错误三分支 | httpx 脚本留档 progress.md |
| 真 Redis 冒烟 | 限流 5 项 IO + 幂等 5 项 IO 全 ALL_PASS | 冒烟脚本用完即删，输出入 progress.md |
| SDK 兼容验收 | `npm run verify` 5/5 exit 0 | scripts/acceptance/ |
| 一键部署 | 三容器健康、空库自动迁移+种子、重复启动幂等 | docker compose |

## 面试深挖准备（对应 docs/notes.md W1+W2 五讲）

- K：令牌桶公式（补 token/拒绝 wait 计算）；Lua 原子性为什么能防竞态；SSE 状态机三件事
- A：为什么哈希不够还要状态/过期校验；鉴权 vs 授权；为什么并发槽用自维护计数
- P：真实踩过的坑——中转渠道 `/v1` 规整、Windows GBK、event_loop fixture 弃用、dependency_overrides vs monkeypatch

## 待办（后续周）

- [ ] W3：计费引擎 + 乐观锁扣减（对账脚本通过）→ 上墙点 1 增「不重扣/不超扣」证据
- [ ] W4：Locust 压测数字（P95、错误率 <1%）→ 上墙点 2 + W4 完整版
- [ ] 英文版迁移提示：动词改强 action 词（Built/Designed/Hand-wrote），量化句保留原样