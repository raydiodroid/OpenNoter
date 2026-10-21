import request from '@/lib/api'

export interface TranscriberConfig {
  transcriber_type: string
  whisper_model_size: string
  available_types: { value: string; label: string }[]
  whisper_model_sizes: string[]
  /** 内置模型映射：size → HF repo_id */
  whisper_builtin_models?: Record<string, string>
  /** 用户自定义模型映射：名称 → HF repo_id 或本地路径 */
  whisper_custom_models?: Record<string, string>
  mlx_whisper_available: boolean
  /** groq 槽位实际指向的端点（可能是 Groq 云端，也可能是自建网关） */
  resolved_endpoint?: string
  /** groq 槽位实际使用的模型名（如 sensevoice） */
  resolved_model?: string
  /** 是否已配置 API Key（用于占位提示，不回传明文） */
  has_asr_key?: boolean
}

/** 更新在线转写端点（地址 / 密钥 / 模型），保存后对新任务立即生效 */
export const updateAsrEndpoint = async (data: {
  base_url: string
  api_key?: string
  model?: string
}) => {
  return await request.post('/update_asr_endpoint', data)
}

export interface ModelStatus {
  model_size: string
  downloaded: boolean
  downloading: boolean
  /** 后台下载失败（仓库 404、网络中断、本地路径缺 model.bin 等）。后端从此字段透传 */
  failed?: boolean
  /** 下载失败时的原因（仅 failed 时存在），用于前端提示 */
  error?: string
}

export interface ModelsStatusResponse {
  whisper: ModelStatus[]
  mlx_whisper: ModelStatus[]
  mlx_available: boolean
}

export const getTranscriberConfig = async (): Promise<TranscriberConfig> => {
  return await request.get('/transcriber_config')
}

export const updateTranscriberConfig = async (data: {
  transcriber_type: string
  whisper_model_size?: string
}) => {
  return await request.post('/transcriber_config', data)
}

export const getModelsStatus = async (): Promise<ModelsStatusResponse> => {
  return await request.get('/transcriber_models_status')
}

export const downloadModel = async (data: {
  model_size: string
  transcriber_type?: string
}) => {
  return await request.post('/transcriber_download', data)
}

export interface WhisperModelsResponse {
  builtin: Record<string, string>
  custom: Record<string, string>
}

/** 列出内置 + 自定义 whisper 模型映射 */
export const listWhisperModels = async (): Promise<WhisperModelsResponse> => {
  return await request.get('/whisper_models')
}

/** 新增自定义模型映射（名称 → HF repo_id 或本地路径） */
export const addWhisperModel = async (data: { name: string; target: string }) => {
  return await request.post('/whisper_models', data)
}

/** 删除自定义模型映射（不会删除已下载的模型文件） */
export const deleteWhisperModel = async (name: string) => {
  return await request.delete(`/whisper_models/${encodeURIComponent(name)}`)
}
