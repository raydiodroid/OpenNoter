import { create } from 'zustand'
import { getAuthConfig, getMe, login as apiLogin, logout as apiLogout, type IUser } from '@/services/auth'
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from '@/lib/api'

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

      // 无论是否强制，都尝试取一次当前用户：已登录时可以显示身份。
      //
      // 注意：桌面端 token 只在内存里，**刷新/重启后必然为空**，所以这里
      // 在没有 token 时不必强求 getMe 成功 —— 失败即视为未登录（见 catch）。
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
    // 登录接口同时返回 token 与 Set-Cookie。Web 端靠 Cookie 即可；
    // 桌面端（tauri://localhost 跨站 + SameSite=Lax）带不上 Cookie，
    // 必须把 token 存进内存，由拦截器以 Authorization: Bearer 下发（F1）。
    setAccessToken(res.token ?? null)
    set({ user: res.user })
  },

  logout: async () => {
    try {
      await apiLogout()
    } finally {
      // 内存 token 必须一并清掉，否则退出后仍带着旧凭据
      setAccessToken(null)
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

// 注册「401 时同步清空登录态」的回调（见 lib/api.ts 里 onUnauthorized 的说明）。
//
// 必须**同步**：401 处理紧接着就会触发整页导航（Web）或 hash 跳转（Tauri），
// 异步的 setState 会赶不上，导致「token 清了但 user 还在」→ AuthGuard 认为仍登录
// → 不跳登录页、可所有请求都 401。这是 F1 原症状的变体，必须在模块加载时就挂好。
setUnauthorizedHandler(() => {
  setAccessToken(null)
  useAuthStore.setState({ user: null })
})

/**
 * 启动时判断「是否已经处于登录态」。
 *
 * Web 端靠同源 Cookie，`getMe()` 成功即已登录；
 * 桌面端只能靠内存 token，而它随进程重启消失 ⇒ 重启后必然是未登录，
 * 需要用户重新输入密码（P0 阶段接受的取舍，安全存储列为后续优化）。
 */
export const hasCredentials = () => Boolean(getAccessToken())
