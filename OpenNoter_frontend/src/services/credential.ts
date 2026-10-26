import request from '@/lib/api'

/**
 * BYOK（用户自带上游凭据）的 API 封装。
 *
 * ## 为什么单独一个文件，不放进 `services/model.ts`
 *
 * `services/model.ts` 里的 11 个函数**全部**指向管理员路由
 * （`POST/PATCH/DELETE /providers`、`POST /models` 等，后端都挂了
 * `require_admin`）。BYOK 的接口是「每个用户管自己的」，权限模型完全不同。
 * 混在一起很容易写错调用目标——普通用户点到管理员接口就是稳定 403。
 *
 * ## 路径不带 `/api` 前缀
 *
 * `lib/api.ts` 的 axios 实例已配 `API_BASE`（含 `/api`），这里写相对路径即可。
 */

interface CallOpts {
  silent?: boolean
}

const cfg = (o?: CallOpts) => (o?.silent ? { suppressToast: true } : undefined)

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

/** 能力位。`default` = 一份凭据覆盖所有能力（推荐）。 */
export type Capability = 'default' | 'llm' | 'asr'

/** 站点模式：只站点 / 可选 / 强制自带。 */
export type ByokMode = 'shared' | 'optional' | 'required'

export interface ByokConfig {
  mode: ByokMode
  allow_custom_base_url: boolean
  updated_at?: string | null
}

export interface CredentialItem {
  capability: Capability
  configured: boolean
  /** 形如 `***ab12`，永不返回明文 */
  masked_key: string
  base_url: string
  model_name: string
  label?: string | null
  /** 配置了但解不开（多半是站点换了加密密钥）→ 前端提示重填 */
  broken?: boolean
}

export interface CapabilityStatus {
  capability: 'llm' | 'asr'
  source: 'own' | 'shared'
  configured: boolean
  masked_key: string
  base_url?: string | null
  model_name?: string | null
  broken?: boolean
}

export interface ByokStatus {
  mode: ByokMode
  allow_custom_base_url: boolean
  byok_enabled: boolean
  capabilities: CapabilityStatus[]
  credentials: CredentialItem[]
}

export interface CredentialPayload {
  base_url: string
  api_key: string
  model_name: string
  label?: string
}

// ---------------------------------------------------------------------------
// 站点模式（读对所有登录用户开放，写仅管理员）
// ---------------------------------------------------------------------------

export const getByokConfig = (opts?: CallOpts) =>
  request.get<never, ByokConfig>('/byok_config', cfg(opts))

export const updateByokConfig = (
  data: { mode?: ByokMode; allow_custom_base_url?: boolean },
  opts?: CallOpts
) => request.patch<never, ByokConfig>('/byok_config', data, cfg(opts))

// ---------------------------------------------------------------------------
// 我的凭据
// ---------------------------------------------------------------------------

export const listMyCredentials = (opts?: CallOpts) =>
  request.get<never, ByokStatus>('/me/credentials', cfg(opts))

export const saveMyCredential = (
  capability: Capability,
  data: CredentialPayload,
  opts?: CallOpts
) => request.put<never, unknown>(`/me/credentials/${capability}`, data, cfg(opts))

export const deleteMyCredential = (capability: Capability, opts?: CallOpts) =>
  request.delete<never, unknown>(`/me/credentials/${capability}`, cfg(opts))

/**
 * 用「尚未保存」的 url + key 拉取模型列表。
 *
 * 不是所有 OpenAI 兼容网关都实现 `/models`（自建 vLLM / 部分中转服务），
 * 所以失败时后端会如实报错，前端应保留手动填写模型名的能力。
 */
export const discoverModels = (
  data: { base_url: string; api_key: string },
  opts?: CallOpts
) =>
  request.post<never, { models: string[] }>(
    '/me/credentials/discover-models',
    data,
    cfg(opts)
  )

export const testMyCredential = (capability: Capability, opts?: CallOpts) =>
  request.post<never, { ok: boolean; latency_ms: number; model: string }>(
    `/me/credentials/${capability}/test`,
    {},
    cfg(opts)
  )
