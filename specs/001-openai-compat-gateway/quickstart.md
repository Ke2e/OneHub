# Quickstart: W1 端到端验收指南

> Phase 1 输出。证明 feature 可用的可复现命令序列——SC-001~005 的执行手册。实现细节见 tasks.md。

## 前置

- Docker Desktop 运行中；Node 20+（验收脚本用）
- `.env`（从 `.env.example` 复制后填）：
  - `DEEPSEEK_API_KEY`（必填，开发者自备）
  - `GATEWAY_API_KEY`（自定一串值，验收脚本要用它）
  - `DATABASE_URL` / `REDIS_URL` / `SECRET_KEY`（example 已给 compose 默认值）

## 一键启动（SC-003：≤2 条命令）

```powershell
# 1. 起全环境（pg + redis + api；api 启动时自动跑迁移 + 幂等种子）
docker compose up -d --build

# 2. 确认健康
curl http://localhost:8000/docs   # 200，交互式 API 文档
```

验收点：/docs 可访问；`docker compose ps` 三服务 healthy；重复执行 up 无需手工清理。

## 协议兼容验收（SC-001/002，JS/TS SDK）

```powershell
cd scripts/acceptance
npm install
npm run verify
```

`verify.mjs` 五项断言（全过 = exit 0）：

1. **非流式**：OpenAI SDK 改 `baseURL: http://localhost:8000/v1` 发对话，拿到 `choices[0].message.content` 非空 + `usage.prompt_tokens > 0`
2. **流式**：`stream: true` 逐块收 delta，正常收尾 `[DONE]`，拼接文本完整
3. **流式 usage**：末尾 chunk `usage.prompt_tokens / completion_tokens` 均为非零正整数（SC-002）
4. **模型列表**：`GET /v1/models` 返回 `data[]` 含 `deepseek-chat`
5. **鉴权**：错误 Key → 401，响应体为 OpenAI 错误结构（`error.message` 存在）

## 错误映射抽查（SC-004）

```powershell
# model 不存在 → 404 + OpenAI 错误结构
curl -H "Authorization: Bearer $env:GATEWAY_API_KEY" http://localhost:8000/v1/chat/completions -d '{"model":"no-such","messages":[{"role":"user","content":"hi"}]}' -H "Content-Type: application/json"

# 缺 Bearer → 401 + OpenAI 错误结构
curl http://localhost:8000/v1/models
```

验收点：5 类异常（坏 body / 未知 model / 无 Key / 错 Key / 上游断连模拟）无一返回堆栈，全部 OpenAI 错误格式。

## 单元测试（保护清单 TDD 证据）

```powershell
cd backend && pytest tests/ -v
```

验收点：SSE 解析、usage 累计、鉴权、错误映射单测全绿（测试先于实现提交——git log 时间序可证）。

## 已知边界（W1 明确不做）

- 限流/幂等/计费/多租户（W2/W3）；通义/智谱与路由熔断（W4）
- Redis W1 仅容器就位无业务读写；`api_key_encrypted` 字段为占位（密钥走 env，W4 加密 ADR）
