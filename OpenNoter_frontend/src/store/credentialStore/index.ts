import { create } from 'zustand'

import {
  deleteMyCredential as apiDelete,
  getByokConfig,
  listMyCredentials,
  saveMyCredential as apiSave,
  type ByokMode,
  type ByokStatus,
  type Capability,
  type CredentialPayload,
} from '@/services/credential'

/**
 * BYOK 状态：站点模式 + 「我的凭据」。
 *
 * 分成两份数据是有意的：
 * - `mode` 决定界面显示什么（shared 模式下整个「我的凭据」区块隐藏）；
 * - `status` 是用户自己的凭据明细（含每个能力位当前用的是谁的）。
 *
 * 两者都从同一个 `GET /me/credentials` 拿（后端在响应里带了 `mode`），
 * 避免前端多打一次请求、也避免两者不一致。
 */
interface CredentialState {
  mode: ByokMode
  allowCustomBaseUrl: boolean
  byokEnabled: boolean
  status: ByokStatus | null
  loading: boolean

  /** 拉取站点模式 + 我的凭据 */
  load: () => Promise<void>
  /** 保存某个能力位的凭据 */
  save: (capability: Capability, payload: CredentialPayload) => Promise<void>
  /** 删除某个能力位的凭据（回到站点默认） */
  remove: (capability: Capability) => Promise<void>
}

const EMPTY_MODE: ByokMode = 'shared'

export const useCredentialStore = create<CredentialState>((set, get) => ({
  mode: EMPTY_MODE,
  allowCustomBaseUrl: true,
  byokEnabled: false,
  status: null,
  loading: false,

  load: async () => {
    set({ loading: true })
    try {
      const data = await listMyCredentials({ silent: true })
      set({
        mode: data.mode,
        allowCustomBaseUrl: data.allow_custom_base_url,
        byokEnabled: data.byok_enabled,
        status: data,
      })
    } catch {
      // 未登录 / 接口不可用时保持默认（shared），不影响主流程
      const cfg = await getByokConfig({ silent: true }).catch(() => null)
      set({
        mode: cfg?.mode ?? EMPTY_MODE,
        allowCustomBaseUrl: cfg?.allow_custom_base_url ?? true,
        byokEnabled: Boolean(cfg && cfg.mode !== 'shared'),
        status: null,
      })
    } finally {
      set({ loading: false })
    }
  },

  save: async (capability, payload) => {
    await apiSave(capability, payload)
    await get().load()
  },

  remove: async capability => {
    await apiDelete(capability)
    await get().load()
  },
}))
