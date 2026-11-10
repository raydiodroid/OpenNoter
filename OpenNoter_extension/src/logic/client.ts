import type { Settings } from './types'

export type Connection = Pick<Settings, 'backendUrl' | 'apiKey'>
export const connectionKey = (config: Connection) => `${config.backendUrl.replace(/\/$/, '')}\0${config.apiKey.trim()}`

export async function connectionScope(config: Connection): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(connectionKey(config)))
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

/** No Vue or storage imports: popup and MV3 worker use the same explicit snapshot. */
export async function apiRequest<T>(config: Connection, path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), path === '/api/chat/ask' ? 120000 : 30000)
  try {
    const res = await fetch(`${config.backendUrl.replace(/\/$/, '')}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(config.apiKey.trim() ? { 'x-api-key': config.apiKey.trim() } : {}),
        ...init?.headers,
      },
    })
    const body = await res.json().catch(() => null)
    if (!res.ok || (body && typeof body.code === 'number' && body.code !== 0)) {
      const message = res.status === 401
        ? '请在插件设置中填写有效的 API Key'
        : body?.msg || body?.detail || `HTTP ${res.status}`
      throw new ApiError(typeof message === 'string' ? message : JSON.stringify(message), res.status)
    }
    return body && 'code' in body ? body.data : body
  }
  finally { clearTimeout(timeout) }
}
