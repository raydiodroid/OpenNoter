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

// ---------------------------------------------------------------------------
// 平台 Cookie（BYOK 的 Cookie 部分）
// ---------------------------------------------------------------------------
//
// 与凭据（user_credentials）维度不同：凭据按「能力位」（llm/asr），
// Cookie 按「平台」（bilibili/douyin/kuaishou/youtube）。所以是独立的
// 端点与类型，不并进上面的结构。

/** 支持配置 Cookie 的平台。与后端 `cookie_policy.COOKIE_PLATFORMS` 一致。 */
export type CookiePlatform = 'bilibili' | 'douyin' | 'kuaishou' | 'youtube'

export interface CookiePlatformStatus {
  platform: CookiePlatform
  /** own = 用户自己的；shared = 站点配置；none = 都没有（降级为无 Cookie） */
  source: 'own' | 'shared' | 'none'
  configured: boolean
  /** 配了但解不开（站点换过加密密钥）→ 提示重填 */
  broken: boolean
  /** 这份 Cookie 大致包含多少条（不含任何值） */
  entry_count: number
  /** 站点是否提供了该平台 Cookie（用户据此判断「不配也能用」） */
  site_available: boolean

  // ---- 过期信息 ----
  /**
   * 已知的真实过期时间（ISO）。只有 netscape 格式（YouTube）可能有值；
   * B站/抖音/快手 用的是请求头 Cookie，本身不含过期时间。
   */
  expires_at?: string | null
  /** 是否**已确认**过期。仅在 expires_at 存在时才可能为 true */
  expired?: boolean
  /** 保存至今的天数（header 格式的主要参考） */
  age_days?: number | null
  /**
   * header 格式保存过久时的「可能失效」提示。
   * 注意：这不是「已过期」——Cookie 也可能因改密码/风控提前失效。
   */
  stale?: boolean
  /** known = 有真实过期时间；unknown = 只能用保存天数参考 */
  expiry_kind?: 'known' | 'unknown'
}

export interface CookieStatus {
  mode: ByokMode
  byok_enabled: boolean
  platforms: CookiePlatformStatus[]
}

export const listMyCookies = (opts?: CallOpts) =>
  request.get<never, CookieStatus>('/me/cookies', cfg(opts))

export const saveMyCookie = (platform: CookiePlatform, cookie: string, opts?: CallOpts) =>
  request.put<never, unknown>(`/me/cookies/${platform}`, { cookie }, cfg(opts))

export const deleteMyCookie = (platform: CookiePlatform, opts?: CallOpts) =>
  request.delete<never, unknown>(`/me/cookies/${platform}`, cfg(opts))

/** Cookie 平台的展示信息（前端 UI 用）。格式与后端一致：header / netscape。 */
export const COOKIE_PLATFORM_META: Record<
  CookiePlatform,
  { label: string; format: 'header' | 'netscape'; hint: string }
> = {
  bilibili: {
    label: 'B 站',
    format: 'header',
    hint: '需要登录态才能拿 1080P+ 与 AI 字幕。格式：SESSDATA=xxx; bili_jct=xxx',
  },
  douyin: {
    label: '抖音',
    format: 'header',
    hint: '抖音免登录可用；配 Cookie 可减少风控拦截。格式：sessionid=xxx; ttwid=xxx',
  },
  kuaishou: {
    label: '快手',
    format: 'header',
    hint: '不配时自动访问首页拿匿名 Cookie；配了更稳定。',
  },
  youtube: {
    label: 'YouTube',
    format: 'netscape',
    hint: '需要 cookies.txt 格式（7 个 tab 分隔字段），用于年龄限制/会员内容。',
  },
}
