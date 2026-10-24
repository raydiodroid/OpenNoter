import request from '@/lib/api'

export interface IUser {
  id: number
  email: string
  name: string | null
  is_superuser: boolean
  is_active: boolean
  has_password: boolean
  oidc_bound: boolean
}

export interface IAuthConfig {
  enforced: boolean
  oidc_enabled: boolean
  issuer: string
}

/** 密钥的生命周期状态。过期 / 停用 / 吊销是三回事，界面要能区分。 */
export type ApiKeyStatus = 'active' | 'expired' | 'disabled' | 'revoked'

export interface IApiKey {
  id: number
  /** 公开、非秘密的稳定 ID */
  key_id: string | null
  name: string
  /** 用途备注：这把 key 存在哪、给谁用 */
  description: string | null
  /** 展示用前缀，形如 sk-bili-cPYYq5MC */
  prefix: string
  /** 明文后 4 位，在密码管理器里肉眼核对 */
  last_four: string | null
  scopes: Record<string, string>
  allowed_ips: string[]
  rate_limit_rpm: number | null
  status: ApiKeyStatus
  revoked: boolean
  revoked_at: string | null
  revoked_reason: string | null
  /** 轮换宽限期内：旧的已吊销但还能用 */
  in_rotation_grace: boolean
  grace_expires_at: string | null
  expires_at: string | null
  /** 距到期天数；null = 永不过期 */
  expires_in_days: number | null
  not_before: string | null
  rotated_from_id: number | null
  last_used_at: string | null
  last_used_ip: string | null
  last_used_endpoint: string | null
  use_count: number
  created_at: string | null
  last_rotated_at: string | null
}

/** 创建 / 克隆时返回的明文（只在这一次出现） */
export interface IApiKeyCreated extends IApiKey {
  key: string
  usage_hint: string
}

export interface IKeyUsage {
  key_id: string | null
  days: number
  total: number
  by_day: { date: string; count: number }[]
  by_endpoint: { path: string; count: number }[]
  recent: {
    method: string
    path: string
    status_code: number | null
    ip: string | null
    duration_ms: number | null
    created_at: string | null
  }[]
}

export interface IKeyInput {
  name: string
  description?: string
  /** 不传 = 永不过期 */
  expires_days?: number | null
  scopes?: Record<string, string>
  allowed_ips?: string[]
  rate_limit_rpm?: number | null
}

export const getAuthConfig = () =>
  request.get<never, IAuthConfig>('/auth/config', { suppressToast: true })

export const getMe = () => request.get<never, IUser>('/auth/me', { suppressToast: true })

export const login = (email: string, password: string) =>
  request.post<never, { token: string; user: IUser }>(
    '/auth/login',
    { email, password },
    { suppressToast: true },
  )

export const logout = () => request.post<never, void>('/auth/logout')

export const changePassword = (password: string, current_password?: string) =>
  request.post<never, void>('/auth/me/password', { password, current_password })

/* ── API 密钥 ─────────────────────────────────────────────── */

export const listKeys = () => request.get<never, IApiKey[]>('/keys')

export const createKey = (data: IKeyInput) =>
  request.post<never, IApiKeyCreated>('/keys', data)

export const updateKey = (id: number, data: Partial<IKeyInput>) =>
  request.patch<never, IApiKey>(`/keys/${id}`, data)

/** 轮换：新建一把继承配置的，旧的按需进入宽限期后吊销 */
export const rotateKey = (id: number, graceDays = 0) =>
  request.post<never, IApiKeyCreated>(`/keys/${id}/rotate`, { grace_days: graceDays })

export const revokeKey = (id: number, reason = 'manual') =>
  request.post<never, IApiKey>(`/keys/${id}/revoke`, { reason })

export const restoreKey = (id: number) => request.post<never, IApiKey>(`/keys/${id}/restore`)

export const getKeyUsage = (id: number, days = 30) =>
  request.get<never, IKeyUsage>(`/keys/${id}/usage`, { params: { days } })

/** 删除：默认是吊销（保留记录供审计），purge=true 才是彻底删除 */
export const deleteKey = (id: number, purge = false) =>
  request.delete<never, void>(`/keys/${id}`, { params: { purge } })

export const listUsers = () =>
  request.get<never, (IUser & { created_at: string | null; last_login_at: string | null })[]>(
    '/auth/admin/users',
  )

/** 改用户属性：后端回传更新后的用户，前端用它校准乐观更新的结果 */
export const updateUser = (id: number, payload: { is_active?: boolean; is_superuser?: boolean }) =>
  request.patch<never, IUser>(`/auth/admin/users/${id}`, payload)

/** 删除用户：账号连同他的 API 密钥一起清除，历史任务保留但解除归属 */
export const deleteUser = (id: number) => request.delete<never, void>(`/auth/admin/users/${id}`)
