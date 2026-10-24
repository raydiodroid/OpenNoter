import { create } from 'zustand'
import { getAuthConfig, getMe, login as apiLogin, logout as apiLogout, type IUser } from '@/services/auth'

interface AuthState {
  /** 初始化完成（无论是否登录），避免首屏闪烁 */
  ready: boolean
  /** 后端是否强制认证（OPENNOTER_AUTH_ENFORCE） */
  enforced: boolean
  oidcEnabled: boolean
  user: IUser | null
  init: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

export const useAuthStore = create<AuthState>(set => ({
  ready: false,
  enforced: false,
  oidcEnabled: false,
  user: null,

  init: async () => {
    try {
      const conf = await getAuthConfig()
      set({ enforced: conf.enforced, oidcEnabled: conf.oidc_enabled })
      // 供 axios 拦截器判断是否该在 401 时跳登录（模块间避免循环依赖）
      ;(window as unknown as { __OPENNOTER_AUTH_ENFORCED__?: boolean }).__OPENNOTER_AUTH_ENFORCED__ =
        conf.enforced

      // 无论是否强制，都尝试取一次当前用户：已登录时可以显示身份
      try {
        const me = await getMe()
        set({ user: me })
      } catch {
        set({ user: null })
      }
    } catch {
      // 后端不可达或老版本没有 /auth/config，按「不强制」处理，避免挡住主流程
      ;(window as unknown as { __OPENNOTER_AUTH_ENFORCED__?: boolean }).__OPENNOTER_AUTH_ENFORCED__ =
        false
      set({ enforced: false, user: null })
    } finally {
      set({ ready: true })
    }
  },

  login: async (email, password) => {
    const res = await apiLogin(email, password)
    set({ user: res.user })
  },

  logout: async () => {
    try {
      await apiLogout()
    } finally {
      set({ user: null })
    }
  },

  refresh: async () => {
    try {
      set({ user: await getMe() })
    } catch {
      set({ user: null })
    }
  },
}))

export const isLoggedIn = () => Boolean(useAuthStore.getState().user)
