import request from '@/lib/api'
import type { ModelItem, RemoteModel } from '@/types'

interface CallOpts {
  silent?: boolean
}

const cfg = (o?: CallOpts) => (o?.silent ? { suppressToast: true } : undefined)

export interface ProviderPayload {
  id?: string
  name?: string
  logo?: string
  type?: string
  api_key?: string
  base_url?: string
  enabled?: number
}

export const getProviderList = (opts?: CallOpts) =>
  request.get<never, ProviderPayload[]>('/providers', cfg(opts))

export const getProviderById = (id: string) =>
  request.get<never, ProviderPayload>(`/providers/${id}`, { suppressToast: true })

export const addProvider = (data: ProviderPayload, opts?: CallOpts) =>
  request.post<never, string>('/providers', data, cfg(opts))

export const updateProvider = (data: ProviderPayload & { id: string }, opts?: CallOpts) =>
  request.patch<never, unknown>(`/providers/${data.id}`, data, cfg(opts))

export const testConnection = (data: { id: string; model?: string }, opts?: CallOpts) =>
  request.post<never, unknown>(`/providers/${data.id}/test`, { model: data.model }, cfg(opts))

export interface ProbeResult {
  ok: boolean
  latency: number
  status?: number
  count?: number
  message?: string
}

/** 轻量探活：只打 GET /models，不消耗 token，LLM / ASR / Embedding 都适用 */
export const probeEndpoint = (data: { base_url: string; api_key?: string }, opts?: CallOpts) =>
  request.post<never, ProbeResult>('/probe_endpoint', data, cfg(opts))

/** 拉取供应商的远端模型列表（形状兼容数组 / { data } / { models }） */
export const fetchRemoteModels = async (providerId: string): Promise<RemoteModel[]> => {
  const res = await request.get<never, any>(`/providers/${providerId}/remote-models`)
  const models = res?.models ?? res
  if (Array.isArray(models)) return models
  if (Array.isArray(models?.data)) return models.data
  return []
}

/** 已启用的模型（全站，用于生成页下拉） */
export const fetchEnabledModels = () => request.get<never, ModelItem[]>('/models')

/** 某个供应商下已启用的模型 */
export const fetchEnabledModelsByProvider = (providerId: string) =>
  request.get<never, ModelItem[]>(`/providers/${providerId}/models`)

export const addModel = (data: { provider_id: string; model_name: string }, opts?: CallOpts) =>
  request.post<never, unknown>('/models', data, cfg(opts))

export const deleteModel = (modelId: number | string) =>
  request.delete<never, unknown>(`/models/${modelId}`)
