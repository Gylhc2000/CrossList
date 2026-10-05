import type {
  AppConfig,
  AuthStatus,
  HistoryJob,
  JobSnapshot,
  MetaResponse,
  PlatformFiles,
  UserMe,
} from './types'

const jsonHeaders = { 'Content-Type': 'application/json' }

/** 带 HTTP 状态码的错误：调用方需要区分「404 任务已不在内存」和普通网络抖动 */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 会话失效时的回调：由 page.tsx 注册，任何请求拿到 401 都统一退回登录页 */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    // 会话在 HttpOnly Cookie 里，JS 读不到也偷不走，但每个请求必须带上
    res = await fetch(path, { credentials: 'include', ...init })
  } catch (e) {
    // fetch 本身失败（断网 / 后端未启动）：status=0，供调用方与 404 区分
    throw new ApiError((e as Error)?.message || '网络请求失败', 0)
  }
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const msg =
      (data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : null) || text || `请求失败（${res.status}）`
    // 会话过期往往是"静默失败"：进度页还在转圈，但每个请求都 401。
    // 在这里统一收口，而不是让每个调用点各自判断一次
    if (res.status === 401 && path !== '/api/me' && !path.startsWith('/api/auth/')) {
      onUnauthorized?.()
    }
    throw new ApiError(msg, res.status)
  }
  return data as T
}

export const api = {
  meta: () => request<MetaResponse>('/api/meta'),

  authStatus: () => request<AuthStatus>('/api/auth/status'),

  register: (body: { username: string; password: string; invite?: string }) =>
    request<{ user: UserMe }>('/api/auth/register', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),

  login: (body: { username: string; password: string }) =>
    request<{ user: UserMe }>('/api/auth/login', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),

  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),

  changePassword: (body: { currentPassword: string; newPassword: string }) =>
    request<{ ok: boolean; revokedSessions: number }>('/api/me/password', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),

  me: () => request<UserMe>('/api/me'),

  myJobs: (limit = 50, offset = 0) =>
    request<{ jobs: HistoryJob[] }>(`/api/me/jobs?limit=${limit}&offset=${offset}`),

  deleteJob: (jobId: string) =>
    request<{ ok: boolean }>(`/api/me/jobs/${jobId}`, { method: 'DELETE' }),

  getConfig: () => request<AppConfig>('/api/config'),

  saveConfig: (patch: Partial<AppConfig>) =>
    request<{ ok: boolean; hasKey: boolean }>('/api/config', {
      method: 'PUT',
      headers: jsonHeaders,
      body: JSON.stringify(patch),
    }),

  testConnection: (body: { baseUrl?: string; apiKey?: string; textModel?: string }) =>
    request<{ ok: boolean; latencyMs?: number; model?: string; error?: string }>(
      '/api/test',
      { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) },
    ),

  createJob: (body: {
    productName: string
    category: string
    description: string
    specs: string
    brand: string
    price: number | null
    currency: string
    platforms: string[]
    markets: string[]
    platformMarkets: Record<string, string[]>
    images: string[]
    withImages: boolean
  }) =>
    request<{ jobId: string }>('/api/jobs', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),

  snapshot: (jobId: string) => request<JobSnapshot>(`/api/jobs/${jobId}`),

  files: (jobId: string) => request<{ platforms: PlatformFiles[] }>(`/api/jobs/${jobId}/files`),

  cancel: (jobId: string) =>
    request<{ ok: boolean }>(`/api/jobs/${jobId}/cancel`, { method: 'POST' }),
}

export const downloadUrl = (jobId: string, scope = 'all') =>
  `/api/jobs/${jobId}/download?scope=${encodeURIComponent(scope)}`

export const streamUrl = (jobId: string) => `/api/jobs/${jobId}/events`

export function fmtSize(n?: number) {
  if (!n) return '0B'
  if (n < 1024) return `${n}B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / 1048576).toFixed(1)}MB`
}
