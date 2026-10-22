import type {
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
import { settings } from './storage'

interface ApiEnvelope<T> {
  code: number
  msg: string
  data: T
}

function backendUrl(): string {
  return (settings.value?.backendUrl || 'http://localhost:8483').replace(/\/$/, '')
}

/**
 * 鉴权头：后端开了强制认证时，除 /sys_health 外的接口都要求凭据。
 * 插件没有浏览器 cookie（cookies 对 localhost 不适用），所以走 API Key。
 */
function authHeaders(): Record<string, string> {
  const key = (settings.value?.apiKey || '').trim()
  return key ? { 'x-api-key': key } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${backendUrl()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    // 401/403 单独给可操作提示，而不是丢一句裸状态码
    if (res.status === 401) {
      throw new Error('HTTP 401：后端已开启强制认证，请在插件设置里填写 API Key')
    }
    if (res.status === 403) {
      throw new Error(`HTTP 403：当前凭据无权访问 ${path}（需要管理员权限）`)
    }
    throw new Error(`HTTP ${res.status}: ${await res.text()}`)
  }
  const body = (await res.json()) as ApiEnvelope<T> | T
  // 后端 ResponseWrapper 包了 {code, msg, data}；非 0 视为业务错
  if (body && typeof body === 'object' && 'code' in body) {
    const env = body as ApiEnvelope<T>
    if (env.code !== 0)
      throw new Error(env.msg || '后端返回失败')
    return env.data
  }
  return body as T
}

export async function getProviders(): Promise<Provider[]> {
  return request<Provider[]>('/api/get_all_providers')
}

export async function getModelsByProvider(providerId: string): Promise<Model[]> {
  return request<Model[]>(`/api/model_enable/${providerId}`)
}

export async function setDownloaderCookie(platform: string, cookie: string): Promise<void> {
  await request('/api/update_downloader_cookie', {
    method: 'POST',
    body: JSON.stringify({ platform, cookie }),
  })
}

export async function getDownloaderCookie(platform: string): Promise<string | null> {
  // 后端：未配置时返回 {code:0, msg:'未找到Cookies', data:null}；配置时 data: {platform, cookie}
  const data = await request<{ platform: string, cookie: string } | null>(
    `/api/get_downloader_cookie/${platform}`,
  )
  return data?.cookie ?? null
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
  await request(`/api/models/delete/${modelId}`)
}

// ---- Transcriber ----
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
  role: 'user' | 'assistant' | 'system'
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
  provider_id: string
  model_name: string
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
  return request<{ task_id: string }>('/api/generate_note', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  // /task_status 永远 HTTP 200；body 是 ResponseWrapper：
  //   成功：{code:0, data:{status, message, task_id, result?}}
  //   任务失败：{code:500, msg:'xxx', data:null}
  // 这里手动拆，把任务失败翻译成 status:'FAILED'，避免 request() 抛错让 UI 收不到状态
  // 必须带鉴权头：强制认证开启后裸 fetch 会 401，进度永远失败
  const res = await fetch(`${backendUrl()}/api/task_status/${taskId}`, {
    headers: {
      ...authHeaders(),
    },
  })
  if (res.status === 401)
    throw new Error('HTTP 401：后端已开启强制认证，请在插件设置里填写 API Key')
  if (res.status === 403)
    throw new Error(`HTTP 403：无权查询任务 ${taskId}`)
  if (!res.ok)
    throw new Error(`HTTP ${res.status}`)
  const body = (await res.json()) as { code: number, msg: string, data: TaskStatusResponse | null }
  if (body.code === 0 && body.data)
    return body.data
  return { status: 'FAILED', message: body.msg || '任务失败', task_id: taskId }
}

export async function ping(): Promise<boolean> {
  try {
    await getProviders()
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
  if (/(hdslb|byteimg|kpcdn|akamaized|ytimg)\.com/i.test(url))
    return `${base}/api/image_proxy?url=${encodeURIComponent(url)}`
  return url
}
