// OneHub 管理台 —— 前端类型定义（对齐后端管理面响应结构）

export interface BreakerState {
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  opened_at: number
}

export interface Channel {
  id: number
  name: string
  provider: string
  base_url: string
  weight: number
  status: string
  failure_count: number
  opened_at: number | null
  created_at: string | null
  breaker_state: BreakerState
}

export interface ChannelPayload {
  name: string
  provider?: string
  base_url?: string
  api_key_encrypted?: string
  weight?: number
  status?: string
}

export interface Overview {
  total_requests: number
  requests_24h: number
  total_tokens: number
  tokens_24h: number
  total_cost: number
  cost_24h: number
  active_channels: number
  channel_count: number
  tenant_balances: { tenant_id: number; balance: number }[]
}

export interface ChannelStat {
  channel_id: number
  requests: number
  tokens: number
  cost: number
  avg_latency_ms: number | null
}

export interface UsageLog {
  id: number
  request_id: string
  api_key_id: number | null
  channel_id: number | null
  model: string
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number | null
  status_code: number
  cost: number
  created_at: string | null
}

export interface UsageLogs {
  items: UsageLog[]
  total: number
  offset: number
  limit: number
}

export interface ApiKey {
  id: number
  name: string | null
  key_prefix: string | null
  tenant_id: number
  model_whitelist: string[] | null
  rpm_limit: number | null
  tpm_limit: number | null
  status: string
  expires_at: string | null
  created_at: string | null
}

export interface Model {
  id: number
  model_name: string
  channel_id: number | null
  input_price: number | null
  output_price: number | null
  enabled: boolean
}

// OpenAI 对话(Playground)
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}