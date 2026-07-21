import { apiRequestCompleted, apiRequestFailed, apiRequestStarted } from '@/lib/logger'

/**
 * 统一的 HTTP 客户端 —— 所有 api/*.ts 模块都应通过 `apiRequest` 发请求,
 * 不要在业务代码里直接 fetch。它集中处理:
 *   - `/api` 前缀与 JSON 头
 *   - 服务端 HttpOnly Cookie 会话透传
 *   - x-request-id 透传 + 结构化请求日志(见 lib/logger)
 *   - 非 2xx 统一抛出 ApiError(带 status,方便上层区分 401 等)
 */

const API_BASE = '/api'
const CSRF_COOKIE_NAME = 'farescope_csrf'

function cookieValue(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  const entry = document.cookie.split('; ').find((part) => part.startsWith(prefix))
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : null
}

function isStateChanging(method: string): boolean {
  return !['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
}

function errorMessage(payload: unknown, status: number): string {
  if (!payload || typeof payload !== 'object') return `HTTP ${status}`
  const body = payload as { detail?: unknown; error?: unknown }
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map((item) => item && typeof item === 'object' && 'msg' in item ? String(item.msg) : '')
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  if (typeof body.error === 'string') return body.error
  return `HTTP ${status}`
}

/** 后端返回非 2xx 时抛出;`status` 让调用方能区分 401 / 404 等。 */
export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | null

  constructor(message: string, status: number, requestId: string | null = null) {
    super(requestId ? `${message}（请求 ID：${requestId}）` : message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method || 'GET'
  const { requestId, startedAt } = apiRequestStarted(path, method)
  let status = 0
  try {
    const csrfToken = isStateChanging(method) ? cookieValue(CSRF_COOKIE_NAME) : null
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        'content-type': 'application/json',
        'x-request-id': requestId,
        ...(csrfToken ? { 'x-csrf-token': csrfToken } : {}),
        ...init?.headers,
      },
    })
    status = response.status
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new ApiError(
        errorMessage(payload, response.status),
        response.status,
        response.headers.get('x-request-id'),
      )
    }
    apiRequestCompleted(path, method, requestId, startedAt, response.status)
    return payload as T
  } catch (error) {
    apiRequestFailed(path, method, requestId, startedAt, error, status)
    throw error
  }
}
