import { create } from 'zustand'
import {
  addProvider as apiAddProvider,
  getProviderById as apiGetProvider,
  getProviderList,
  updateProvider as apiUpdateProvider,
  type ProviderPayload,
} from '@/services/model'
import type { Provider } from '@/types'

const mapProvider = (item: ProviderPayload): Provider => ({
  id: String(item.id),
  name: item.name ?? '',
  logo: item.logo ?? 'custom',
  apiKey: item.api_key ?? '',
  baseUrl: item.base_url ?? '',
  type: item.type ?? 'custom',
  enabled: item.enabled ?? 0,
})

interface ProviderState {
  providers: Provider[]
  loading: boolean
  fetchAll: () => Promise<void>
  fetchOne: (id: string) => Promise<Provider | null>
  create: (input: Omit<Provider, 'id' | 'enabled'>) => Promise<string>
  update: (provider: Provider) => Promise<void>
}

export const useProviderStore = create<ProviderState>((set, get) => ({
  providers: [],
  loading: false,

  fetchAll: async () => {
    set({ loading: true })
    try {
      const list = await getProviderList()
      set({ providers: (list ?? []).map(mapProvider) })
    } catch {
      set({ providers: [] })
    } finally {
      set({ loading: false })
    }
  },

  fetchOne: async id => {
    try {
      const item = await apiGetProvider(id)
      return item ? mapProvider(item) : null
    } catch {
      return null
    }
  },

  create: async input => {
    const id = await apiAddProvider({
      name: input.name,
      api_key: input.apiKey,
      base_url: input.baseUrl,
      type: input.type,
      logo: input.logo,
    })
    await get().fetchAll()
    return String(id)
  },

  update: async provider => {
    await apiUpdateProvider({
      id: provider.id,
      name: provider.name,
      api_key: provider.apiKey,
      base_url: provider.baseUrl,
      type: provider.type,
      logo: provider.logo,
      enabled: provider.enabled,
    })
    await get().fetchAll()
  },
}))
