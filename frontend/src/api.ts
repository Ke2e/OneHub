// OneHub 管理台 —— 后端 API 封装（原生 fetch，JWT 存 localStorage）
import type {
  ApiKey,
  Channel,
  ChannelPayload,
  ChannelStat,
  Model,
  Overview,
  UsageLogs,
} from './types'

const TOKEN_KEY = 'onehub_admin_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(path, { ...init, headers })
  if (resp.status === 204) return undefined as T

  const payload = await resp.json().catch(() => null)
  if (!resp.ok) {
    const msg =
      payload?.error?.message ||
      payload?.detail ||
      `请求失败(${resp.status})`
    if (resp.status === 401 && path !== '/api/auth/login') {
      clearToken()
    }
    throw new ApiError(resp.status, msg, payload?.error?.code)
  }
  return payload as T
}

const j = (v: unknown) => JSON.stringify(v)

// ── 认证 ──
export const authApi = {
  login: (data: { email: string; password: string }) =>
    request<{ token: string; user: { id: number; email: string; role: string } }>(
      '/api/auth/login',
      { method: 'POST', body: j(data) },
    ),
}

// ── 渠道 ──
export const channelApi = {
  list: () => request<{ items: Channel[] }>('/api/channels'),
  create: (data: ChannelPayload) =>
    request<Channel>('/api/channels', { method: 'POST', body: j(data) }),
  update: (id: number, data: Partial<ChannelPayload>) =>
    request<Channel>(`/api/channels/${id}`, { method: 'PATCH', body: j(data) }),
  remove: (id: number) => request<void>(`/api/channels/${id}`, { method: 'DELETE' }),
}

// ── 模型定价 ──
export const modelApi = {
  list: () => request<{ items: Model[] }>('/api/models'),
  create: (data: Partial<Model>) =>
    request<Model>('/api/models', { method: 'POST', body: j(data) }),
  update: (id: number, data: Partial<Model>) =>
    request<Model>(`/api/models/${id}`, { method: 'PATCH', body: j(data) }),
  remove: (id: number) => request<void>(`/api/models/${id}`, { method: 'DELETE' }),
}

// ── API Key ──
export const keyApi = {
  list: () => request<{ items: ApiKey[] }>('/api/keys'),
  create: (data: {
    name?: string
    model_whitelist?: string[]
    expires_at?: string
  }) => request<ApiKey & { key?: string }>('/api/keys', { method: 'POST', body: j(data) }),
}

// ── 仪表盘 ──
export const dashboardApi = {
  overview: () => request<Overview>('/api/dashboard/overview'),
  channels: () => request<{ items: ChannelStat[] }>('/api/dashboard/channels'),
  logs: (params: { model?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams()
    if (params.model) q.set('model', params.model)
    q.set('limit', String(params.limit ?? 20))
    q.set('offset', String(params.offset ?? 0))
    return request<UsageLogs>(`/api/usage/logs?${q.toString()}`)
  },
}

// ── Playground 代理 ──
export const playApi = {
  chat: async (data: {
    model: string
    messages: { role: string; content: string }[]
    stream?: boolean
  }) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
    return fetch('/api/play/chat', { method: 'POST', headers, body: j(data) })
  },
}