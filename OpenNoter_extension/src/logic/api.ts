import type {
  AsrProvider,
  AsrProviderInput,
  DeployStatus,
  GenerateRequest,
  Model,
  Provider,
  ProviderCreatePayload,
  ProviderUpdatePayload,
  TaskStatusResponse,
  TranscriberConfig,
  TranscriberModelsStatus,
  TranscriberType,
  WhisperModelSize,
} from './types'
import { settings, settingsReady } from './storage'
import { ApiError, apiRequest, connectionKey } from './client'

function backendUrl(): string {
  return (settings.value?.backendUrl || 'http://localhost:8483').replace(/\/$/, '')
}

export function authHeaders(): Record<string, string> {
  const key = settings.value.apiKey.trim()
  return key ? { 'x-api-key': key } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  await settingsReady
  const config = { ...settings.value }
  const result = await apiRequest<T>(config, path, init)
  if (connectionKey(config) !== connectionKey(settings.value))
    throw new ApiError('连接设置已改变，请重新操作', 409)
  return result
}

export async function getProviders(): Promise<Provider[]> {
  return request<Provider[]>('/api/get_all_providers')
}

export async function getModelsByProvider(providerId: string): Promise<Model[]> {
  return request<Model[]>(`/api/model_enable/${providerId}`)
}

/**
 * 全站可用模型（含当前用户 BYOK 凭据里的模型）。
 *
 * 与 ``getModelsByProvider`` 的区别：
 *   - 那个按「站点供应商」查，返回 ``{id, model_name}``；
 *   - 这个走 ``GET /api/models``，响应项带 ``source``（shared/own），
 *     BYOK 用户配的模型 ``provider_id`` 形如 ``own:llm``。
 *
 * 扩展端用它做「模型的唯一真相来源」，避免普通用户在 BYOK 模式下
 * 看不到自己的模型。
 */
export interface AvailableModel {
  id: string | number
  provider_id: string
  model_name: string
  source?: 'shared' | 'own'
}

export async function listAvailableModels(): Promise<AvailableModel[]> {
  return request<AvailableModel[]>('/api/models')
}

export interface CookieStatus {
  mode: string
  byok_enabled: boolean
  platforms: Array<{ platform: string, configured: boolean, source: string, entry_count: number }>
}
export const getCookieStatus = () => request<CookieStatus>('/api/me/cookies')
export const getCurrentUser = () => request<{ id: number, is_superuser: boolean }>('/api/auth/me')
export const getByokConfig = () => request<{ mode: string, cookie_mode: string }>('/api/byok_config')

export async function setDownloaderCookie(platform: string, cookie: string): Promise<void> {
  const config = { ...settings.value }
  const policy = await request<{ cookie_mode: string }>('/api/byok_config')
  if (connectionKey(config) !== connectionKey(settings.value))
    throw new ApiError('连接设置已改变', 409)
  if (policy.cookie_mode !== 'shared') {
    await request(`/api/me/cookies/${encodeURIComponent(platform)}`, {
      method: cookie.trim() ? 'PUT' : 'DELETE',
      ...(cookie.trim() ? { body: JSON.stringify({ cookie }) } : {}),
    })
  }
  else {
    await request('/api/update_downloader_cookie', { method: 'POST', body: JSON.stringify({ platform, cookie }) })
  }
}

export async function getDownloaderCookie(platform: string): Promise<string | null> {
  const policy = await request<{ cookie_mode: string }>('/api/byok_config')
  if (policy.cookie_mode !== 'shared')
    return null // Own cookies are intentionally never disclosed.
  const data = await request<{ platform: string, cookie: string } | null>(`/api/get_downloader_cookie/${platform}`)
  return data?.cookie ?? null
}

export interface CredentialStatus {
  mode: string
  credentials: Array<{ capability: string, base_url: string, model_name: string, masked_key: string }>
}
export const getMyCredentials = () => request<CredentialStatus>('/api/me/credentials')
export function saveMyCredential(capability: string, data: { base_url: string, api_key: string, model_name: string }) {
  return request(`/api/me/credentials/${capability}`, { method: 'PUT', body: JSON.stringify(data) })
}
export function deleteMyCredential(capability: string) {
  return request(`/api/me/credentials/${capability}`, { method: 'DELETE' })
}

// ---- Provider CRUD ----
export async function addProvider(payload: ProviderCreatePayload): Promise<string | null> {
  return request<string | null>('/api/add_provider', {
    method: 'POST',
    body: JSON.stringify({ logo: 'custom', ...payload }),
  })
}

export async function updateProvider(payload: ProviderUpdatePayload): Promise<{ id: string, enabled: number }> {
  return request<{ id: string, enabled: number }>('/api/update_provider', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getProviderById(id: string): Promise<Provider> {
  return request<Provider>(`/api/get_provider_by_id/${id}`)
}

export async function connectTest(id: string): Promise<void> {
  await request('/api/connect_test', {
    method: 'POST',
    body: JSON.stringify({ id }),
  })
}

// ---- Model CRUD ----
export async function listAllModels(providerId: string): Promise<Model[]> {
  return request<Model[]>(`/api/model_list/${providerId}`)
}

export async function addModel(providerId: string, modelName: string): Promise<void> {
  await request('/api/models', {
    method: 'POST',
    body: JSON.stringify({ provider_id: providerId, model_name: modelName }),
  })
}

export async function deleteModel(modelId: number | string): Promise<void> {
  // 后端只保留 `DELETE /api/models/{id}`（旧的 `/api/models/delete/{id}` 已下线）。
  // 用旧路径会稳定 404 —— 表现是「设置页删模型点了没反应」。
  await request(`/api/models/${modelId}`, { method: 'DELETE' })
}

// ---- Transcriber ----
export function createAsrProvider(data: AsrProviderInput): Promise<AsrProvider> {
  return request('/api/asr/providers', { method: 'POST', body: JSON.stringify(data) })
}
export function updateAsrProvider(id: string, data: Partial<AsrProviderInput>): Promise<AsrProvider> {
  return request(`/api/asr/providers/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(data) })
}
export function deleteAsrProvider(id: string) {
  return request(`/api/asr/providers/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function updateAsrEndpoint(data: { base_url: string, api_key?: string, model?: string }) {
  return request('/api/update_asr_endpoint', { method: 'POST', body: JSON.stringify(data) })
}

export async function getTranscriberConfig(): Promise<TranscriberConfig> {
  return request<TranscriberConfig>('/api/transcriber_config')
}

export async function setTranscriberConfig(transcriberType: TranscriberType, whisperModelSize?: WhisperModelSize): Promise<TranscriberConfig> {
  return request<TranscriberConfig>('/api/transcriber_config', {
    method: 'POST',
    body: JSON.stringify({
      transcriber_type: transcriberType,
      whisper_model_size: whisperModelSize ?? null,
    }),
  })
}

export async function getTranscriberModelsStatus(): Promise<TranscriberModelsStatus> {
  return request<TranscriberModelsStatus>('/api/transcriber_models_status')
}

export async function downloadTranscriberModel(modelSize: WhisperModelSize, transcriberType: TranscriberType = 'fast-whisper'): Promise<void> {
  await request('/api/transcriber_download', {
    method: 'POST',
    body: JSON.stringify({ model_size: modelSize, transcriber_type: transcriberType }),
  })
}

// ---- RAG Chat ----
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export async function indexChatTask(taskId: string): Promise<void> {
  await request('/api/chat/index', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  })
}

export async function getChatStatus(taskId: string): Promise<{ status: 'idle' | 'indexing' | 'indexed' | 'failed', indexed: boolean }> {
  return request(`/api/chat/status?task_id=${encodeURIComponent(taskId)}`)
}

export async function askChat(payload: {
  task_id: string
  question: string
  history: ChatMessage[]
  provider_id?: string
  model_name?: string
}): Promise<unknown> {
  return request('/api/chat/ask', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ---- Monitor ----
export async function getDeployStatus(): Promise<DeployStatus> {
  return request<DeployStatus>('/api/deploy_status')
}

export interface SysHealth {
  ok: boolean
  msg?: string
  /** 后端返回的结构化子项：'ok' | 'missing' | 'error' */
  ffmpeg?: string
  db?: string
  whisper?: { size: string | null, type: string | null, downloaded: boolean, checked: boolean }
}

/**
 * 读 /sys_health。
 *
 * 之前这里把结构化 payload 丢成 `ok: true` 布尔，导致 ffmpeg='missing' /
 * db='error' 这类「后端活着但依赖坏了」的状态在插件里永远看不见
 * （审计结论：扩展侧有装饰性指标）。现在原样带出子项，让 UI 能如实区分。
 */
export async function getSysHealth(): Promise<SysHealth> {
  try {
    const data = await request<{
      ffmpeg?: string
      db?: string
      whisper_model?: { size: string | null, type: string | null, downloaded: boolean, checked: boolean }
    }>('/api/sys_health')
    return {
      ok: true,
      ffmpeg: data?.ffmpeg,
      db: data?.db,
      whisper: data?.whisper_model,
    }
  }
  catch (e) {
    return { ok: false, msg: (e as Error).message }
  }
}

export async function generateNote(payload: GenerateRequest): Promise<{ task_id: string }> {
  return request<{ task_id: string }>('/api/notes', {
    method: 'POST',
    body: JSON.stringify({ ...payload, url: payload.video_url, provider_id: payload.provider_id || undefined, model_name: payload.model_name || undefined }),
  })
}

export async function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>(`/api/tasks/${encodeURIComponent(taskId)}`)
}

export async function ping(): Promise<boolean> {
  try {
    // This user-facing endpoint works in both anonymous and authenticated deployments.
    await request('/api/models')
    return true
  }
  catch {
    return false
  }
}

// markdown 里的 /static/screenshots/xxx 是相对路径，extension 渲染时需要拼绝对地址
export function absolutizeMarkdownImages(md: string): string {
  const base = backendUrl()
  return md.replace(/!\[([^\]]*)\]\((\/static\/[^)]+)\)/g, (_, alt, path) => `![${alt}](${base}${path})`)
}

// backend 用 note_helper 在笔记开头插一行 '> 来源链接：URL'。侧边栏顶部已经有原片链接卡片，
// 渲染前把它剥掉，避免重复占位。复制/下载的 .md 保留原样以便溯源。
// 与 OpenNoter_frontend/src/pages/HomePage/components/MarkdownViewer.tsx:468 对齐
export function stripSourceLink(md: string): string {
  return md.replace(/^>\s*来源链接：[^\n]*\n*/m, '')
}

// 单个图片 URL 的处理：相对路径 → 拼后端域名；B 站等带防盗链的封面 → 走后端 image_proxy
export function resolveImageUrl(url: string | undefined | null): string {
  if (!url)
    return ''
  const base = backendUrl()
  if (url.startsWith('/'))
    return `${base}${url}`
  // B 站封面、抖音封面等会做 referer 校验；走后端代理
  if (/(?:hdslb|byteimg|kpcdn|akamaized|ytimg)\.com/i.test(url))
    return `${base}/api/image_proxy?url=${encodeURIComponent(url)}`
  return url
}
