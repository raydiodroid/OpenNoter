import { create } from 'zustand'
import { IProvider } from '@/types'
import { getProviderList } from '@/services/model.ts'

interface ProviderStore {
  provider: IProvider[]
  setProvider: (provider: IProvider) => void
  setAllProviders: (providers: IProvider[]) => void
  getProviderById: (id: number) => IProvider | undefined
  getProviderList: () => IProvider[]
  fetchProviderList: () => Promise<void>
}

export const useProviderStore = create<ProviderStore>((set, get) => ({
  provider: [],


  // 添加或更新一个 provider
  setProvider: newProvider =>
    set(state => {
      const exists = state.provider.find(p => p.id === newProvider.id)
      if (exists) {
        return {
          provider: state.provider.map(p => (p.id === newProvider.id ? newProvider : p)),
        }
      } else {
        return { provider: [...state.provider, newProvider] }
      }
    }),

  // 设置整个 provider 列表
  setAllProviders: providers => set({ provider: providers }),

  // 按 id 获取单个 provider
  getProviderById: id => get().provider.find(p => p.id === id),

  getProviderList: () => get().provider,
  fetchProviderList: async () => {
    try {
      const res = await getProviderList()
      if (res.data.code === 0) {
        set({
          provider: res.data.data.map(
            (item: {
              id: string
              name: string
              logo: string
              api_key: string
              base_url: string
            }) => {
              return {
                id: item.id,
                name: item.name,
                logo: item.logo,
                apiKey: item.api_key,
                baseUrl: item.base_url,
                type: item.type,
              }
            }
          ),
        })
      }
    } catch (error) {
      console.error('Error fetching provider list:', error)
    }
  },
}))
