// Verify.mjs —— OneHub W1 JS SDK 验收脚本（T025）
// 用 openai SDK 改 base_url 打真实网关，五断言全过 = exit 0（SC-001/002/004/005 证据）。
// 任一失败 exit 1。模型 ID 用种子实际 ID（SenseAudio 平台），勿用 quickstart 旧 ID。
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import OpenAI from "openai";

// ---- 配置 ----
// GATEWAY_BASE_URL：网关地址。不用 .env 的 BASE_URL（那是渠道上游地址，W1 键名决策，
// 直接读会污染本脚本）；GATEWAY_API_KEY 优先环境变量，回退从根 .env 手写解析提取（不打印值）。
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const parseDotEnv = () => {
  const out = {};
  try {
    const raw = readFileSync(path.join(ROOT, ".env"), "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch { /* 无 .env 时依赖环境中已有的变量 */ }
  return out;
};

const BASE_URL = process.env.GATEWAY_BASE_URL || "http://localhost:8000/v1";
const KEY = process.env.GATEWAY_API_KEY ?? parseDotEnv().GATEWAY_API_KEY;
const MODEL = process.env.MODEL_ID || "deepseek-v4-flash-0731"; // 种子实际 ID
const MODEL2 = process.env.MODEL_ID_2 || "senseaudio-s2";       // 种子实际 ID

if (!KEY) {
  console.error("[FAIL] GATEWAY_API_KEY 未设置（环境变量或根 .env 缺失）");
  process.exit(1);
}

let failures = 0;
const check = (name, ok, detail = "") => {
  if (ok) console.log(`  [PASS] ${name}`);
  else { console.error(`  [FAIL] ${name}${detail ? ` — ${detail}` : ""}`); failures++; }
};

const client = new OpenAI({ baseURL: BASE_URL, apiKey: KEY });

console.log(`\n== 1/5 非流式对话（SC-001） ==`);
try {
  const r = await client.chat.completions.create({
    model: MODEL,
    messages: [{ role: "user", content: "用一句话自我介绍" }],
    max_tokens: 64,
  });
  const content = r.choices?.[0]?.message?.content ?? "";
  check("非流式 choices[0].message.content 非空", typeof content === "string" && content.trim().length > 0);
  check("非流式 usage.prompt_tokens > 0", Number(r.usage?.prompt_tokens) > 0, `usage=${JSON.stringify(r.usage)}`);
} catch (e) {
  check("非流式对话", false, String(e?.message ?? e));
}

console.log(`\n== 2/5 流式 delta 拼接 + [DONE] 收尾（SC-001） ==`);
console.log(`\n== 3/5 流式末尾 usage 非零（SC-002，网关注入 include_usage，调用方零配置） ==`);
try {
  // 不显式传 stream_options——验证网关在 T011 无条件注入后，SDK 无需任何配置即可拿 usage
  const stream = await client.chat.completions.create({
    model: MODEL,
    messages: [{ role: "user", content: "请返回固定字符串：ACC-PASS-OK" }],
    stream: true,
    max_tokens: 64,
  });
  let joined = "";
  let finished = false;
  let lastUsage = null;
  for await (const chunk of stream) {
    if (chunk.usage) lastUsage = chunk.usage;
    const delta = chunk.choices?.[0]?.delta?.content;
    if (delta) joined += delta;
    if (chunk.choices?.[0]?.finish_reason) finished = true;
  }
  check("流式 delta 拼接完整且正常收尾", joined.includes("ACC-PASS-OK") && finished, `joined=${JSON.stringify(joined.slice(0, 60))} finished=${finished}`);
  const u = lastUsage;
  check("流式末尾 usage 全字段非零正整数", u && Number(u.prompt_tokens) > 0 && Number(u.completion_tokens) > 0 && Number(u.total_tokens) > 0, `usage=${JSON.stringify(u)}`);
} catch (e) {
  check("流式对话", false, String(e?.message ?? e));
}

console.log(`\n== 4/5 模型列表含两实际 ID（US4） ==`);
try {
  const list = await client.models.list();
  const ids = list.data.map((m) => m.id);
  check("data[] 含 deepseek-v4-flash-0731 + senseaudio-s2", ids.includes(MODEL) && ids.includes(MODEL2), `ids=[${ids.join(", ")}]`);
} catch (e) {
  check("models list", false, String(e?.message ?? e));
}

console.log(`\n== 5/5 错误 Key → 401 + OpenAI 错误结构（SC-004） ==`);
try {
  await new OpenAI({ baseURL: BASE_URL, apiKey: "sk-invalid-acceptance-key" })
    .chat.completions.create({
      model: MODEL,
      messages: [{ role: "user", content: "hi" }],
    });
  check("错 key 应抛 401", false, "请求未抛错");
} catch (e) {
  const body = e?.error ?? {};
  const msg = body?.error?.message ?? body?.message;
  check("错 key 401 + error.message 存在", e?.status === 401 && typeof msg === "string" && msg.length > 0, `status=${e?.status} err=${e?.name}`);
}

// 收尾：先关闭 SDK 底层连接再退出，避免 Windows/libuv 在 process.exit 时对未关句柄断言崩溃
try { await client.close(); } catch { /* 客户端未创建成功时无需关闭 */ }
console.log(failures === 0 ? "\nALL ASSERTIONS PASSED (exit 0)" : `\n${failures} ASSERTION(S) FAILED (exit 1)`);
process.exitCode = failures === 0 ? 0 : 1;