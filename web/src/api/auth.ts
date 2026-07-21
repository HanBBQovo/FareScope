import { ApiError, apiRequest } from '@/api/client'

/** 鉴权接口使用服务端 HttpOnly Cookie；前端不保存或伪造会话。 */

export interface SessionUser {
  id: string
  username: string
  email: string | null
  displayName: string
}

export interface AuthSession {
  authenticated: boolean
  user: SessionUser | null
  mode: 'live'
}

export const sessionQueryKey = ['auth', 'session'] as const

interface BackendUser {
  id: string
  username: string
  email: string | null
  display_name: string
}

interface BackendAuthenticatedUser {
  user: BackendUser
}

function liveSession(payload: BackendAuthenticatedUser): AuthSession {
  return {
    authenticated: true,
    mode: 'live',
    user: {
      id: payload.user.id,
      username: payload.user.username,
      email: payload.user.email,
      displayName: payload.user.display_name,
    },
  }
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const payload = await apiRequest<BackendAuthenticatedUser>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  return liveSession(payload)
}

export async function register(username: string, password: string): Promise<AuthSession> {
  const payload = await apiRequest<BackendAuthenticatedUser>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  return liveSession(payload)
}

export async function logout(): Promise<void> {
  await apiRequest('/auth/logout', { method: 'POST', body: '{}' })
}

export async function getSession(): Promise<AuthSession> {
  try {
    return liveSession(await apiRequest<BackendAuthenticatedUser>('/auth/me'))
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return { authenticated: false, user: null, mode: 'live' }
    }
    throw error
  }
}
