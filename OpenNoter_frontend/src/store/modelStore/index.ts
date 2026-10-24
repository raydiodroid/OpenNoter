import { create } from 'zustand'
import {
  addModel as apiAddModel,
  deleteModel as apiDeleteModel,
  fetchEnabledModels,
  fetchEnabledModelsByProvider,
  fetchRemoteModels,
} from '@/services/model'
import type { ModelItem, RemoteModel } from '@/types'

interface ModelState {
  /** 全站已启用模型，生成页下拉用 */
  enabled: ModelItem[]
  /** 当前供应商的远端模型（可勾选） */
  remote: RemoteModel[]
  /** 当前供应商已启用模型 */
  providerModels: ModelItem[]
  loadingEnabled: boolean
  loadingRemote: boolean
  loadEnabled: () => Promise<void>
  loadRemote: (providerId: string) => Promise<void>
  loadProviderModels: (providerId: string) => Promise<void>
  addModel: (providerId: string, modelName: string) => Promise<void>
  removeModel: (id: number | string, providerId: string) => Promise<void>
}

export const useModelStore = create<ModelState>((set, get) => ({
  enabled: [],
  remote: [],
  providerModels: [],
  loadingEnabled: false,
  loadingRemote: false,

  loadEnabled: async () => {
    set({ loadingEnabled: true })
    try {
      const list = await fetchEnabledModels()
      set({ enabled: Array.isArray(list) ? list : [] })
    } catch {
      set({ enabled: [] })
    } finally {
      set({ loadingEnabled: false })
    }
  },

  loadRemote: async providerId => {
    if (!providerId) return
    set({ loadingRemote: true })
    try {
      set({ remote: await fetchRemoteModels(providerId) })
    } catch {
      set({ remote: [] })
    } finally {
      set({ loadingRemote: false })
    }
  },

  loadProviderModels: async providerId => {
    if (!providerId) return
    try {
      const list = await fetchEnabledModelsByProvider(providerId)
      set({ providerModels: Array.isArray(list) ? list : [] })
    } catch {
      set({ providerModels: [] })
    }
  },

  addModel: async (providerId, modelName) => {
    await apiAddModel({ provider_id: providerId, model_name: modelName })
    await get().loadProviderModels(providerId)
    await get().loadEnabled()
  },

  removeModel: async (id, providerId) => {
    await apiDeleteModel(id)
    await get().loadProviderModels(providerId)
    await get().loadEnabled()
  },
}))
