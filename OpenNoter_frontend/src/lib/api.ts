import axios, { AxiosInstance, AxiosResponse } from 'axios'
import { toast } from '@/lib/toast'
import { API_BASE, isTauri } from '@/lib/urls'

// 纯 URL 工具已拆到零依赖的 `lib/urls.ts`（见那里的注释），这里原样回导，
// 让所有既有 `import { apiUrl } from '@/lib/api'` 的调用点一行都不用改。
export * from '@/lib/urls'

/**
 * 后端统一响应包装：{ code, msg, data }。
 * 全站只有这一种响应体形状 —— 成功、业务失败、参数校验失败都是它。
 * HTTP 状态码同时表达成败（4xx / 5xx），code 用于细分业务原因。
 */
export interface IResponse<T = unknown> {
  code: number
  msg: string
  data: T
}

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 预期内的失败（探活、轮询、onboarding 撞名）不弹全局提示，由调用方自己处理 */
    suppressToast?: boolean
  }
}

// ---------------------------------------------------------------------------
// 桌面端凭据：内存 token（F1）
// ---------------------------------------------------------------------------
/**
 * ## 为什么桌面端不能靠 Cookie
 *
 * 登录接口同时返回 JSON `token` 和 `Set-Cookie: opennoter_token`（HttpOnly、
 * `SameSite=Lax`）。Web 端两者同源，Cookie 能正常带上，这条路径是好的。
 *
 * 但 Tauri 的页面源是 `tauri://localhost`（或 `https://tauri.localhost`），
 * 而 API 在 `http://127.0.0.1:8483` —— **跨站 + SameSite=Lax ⇒ 浏览器不会带 Cookie**。
 * 于是默认 `AUTH_ENFORCE=true` 时，桌面端登录"成功"后所有请求都是 401，
 * 而 UI 仍然显示已登录（登录接口本身返回了 user）。
 *
 * 修法：桌面端把 token 放**内存**并用 `Authorization: Bearer` 下发。
 *
 * - 存内存而不是 localStorage：桌面端 localStorage 同样是跨站存储，且 XSS 可直接读；
 *   内存态随进程退出消失，暴露面最小。
 * - **不开 `withCredentials`**：Tauri 跨站，开了也不生效，反而在 Web 端引入
 *   CORS 凭据复杂度（后端还得精确回显 Origin）。
 * - 代价：桌面端刷新/重启后需要重新登录。这是 P0 阶段接受的取舍，
 *   安全存储（Tauri stronghold / OS keychain）列为后续优化。
 */
let accessToken: string | null = null

/** 登录成功后由 authStore 调用，把 token 交给请求层 */
export function setAccessToken(token: string | null) {
  accessToken = token
}

/** 供 authStore 判断当前是否有内存态凭据 */
export function getAccessToken(): string | null {
  return accessToken
}

/**
 * 401 时的「同步清空登录态」回调。
 *
 * ## 为什么需要注册机制而不是直接 import
 *
 * `api.ts` 与 `authStore` 互相依赖（store 用 request，request 要清 store），
 * 直接 import 会成环。而**异步** import（`void import().then(...)`）有个致命问题：
 * 401 处理里紧接着就是 `redirectToLogin()`，Web 分支会立刻整页导航 ——
 * 微任务里的 `setState` 大概率来不及执行，页面就卸载了。
 * 于是 `accessToken` 被清了、但 store 里的 `user` 仍是旧值，
 * `AuthGuard`（只看 `user`）判定"已登录" ⇒ 不跳登录页、但所有请求 401。
 *
 * 所以改成：authStore 在模块加载时**注册一个同步回调**，401 里直接同步调用。
 */
let onUnauthorized: (() => void) | null = null

/**
 * 由 authStore 在模块加载时注册（同步执行，不得是 async）。
 * 传入 null 可注销（测试用）。
 */
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

/**
 * 401 之后跳登录页。
 *
 * ## 为什么用 hash（F11）
 *
 * 桌面端用 `HashRouter`，路由在 `#/login`。若这里写
 * `window.location.href = '/login'`，浏览器会整页导航到
 * `tauri://localhost/login` —— 既离开 hash 路由，也没有对应资源，等于白屏。
 * 所以桌面端改成改 hash；Web 端保持原来的整页跳转（BrowserRouter 下是对的）。
 */
function redirectToLogin() {
  if (isTauri()) {
    if (!window.location.hash.startsWith('#/login')) {
      window.location.hash = '#/login'
    }
    return
  }
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

const request: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
})

// 请求拦截器：桌面端有 token 就带 Authorization
request.interceptors.request.use(config => {
  if (accessToken) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

request.interceptors.response.use(
  ((response: AxiosResponse<IResponse>) => {
    const res = response.data
    if (res?.code === 0) {
      return res.data
    }
    // 2xx 却带了非 0 的 code：业务层主动报错
    if (!response.config?.suppressToast) {
      toast.error(res?.msg || '操作失败，请稍后再试')
    }
    return Promise.reject(res)
  }) as any,
  (error) => {
    const suppress = error?.config?.suppressToast === true
    const status = error?.response?.status
    const payload = error?.response?.data
    const url: string = error?.config?.url || ''

    // 401：仅在「后端强制认证」时跳登录，避免未开启鉴权的环境被拽去登录页
    if (status === 401) {
      const isAuthProbe =
        url.includes('/auth/config') || url.includes('/auth/me') || url.includes('/auth/login')
      const enforced = (window as unknown as { __OPENNOTER_AUTH_ENFORCED__?: boolean })
        .__OPENNOTER_AUTH_ENFORCED__ === true
      if (enforced && !isAuthProbe) {
        // 内存 token 已失效，先同步清掉，避免后续请求继续带一个坏 token。
        // 注意 authStore 的清理**不能**依赖异步 import 后再 set：
        //   * `void import().then()` 是微任务，而下一行 redirectToLogin() 是同步的；
        //   * Web 分支会立刻整页导航，微任务大概率还没跑完页面就卸载了；
        //   * 结果：accessToken 清了、`user` 还是旧值 → AuthGuard 只看 `user`
        //     ⇒ 不跳登录页、但所有请求 401（这正是 F1 原症状的变体）。
        //
        // 所以这里登记一个「待清空」契约，由 authStore 在模块加载时注册同步回调；
        // 回调未注册（极端情况：authStore 还没被 import）时退化为延迟清理 + 强制跳转。
        const hadToken = Boolean(accessToken)
        accessToken = null
        if (hadToken || enforced) {
          if (onUnauthorized) {
            onUnauthorized() // 同步：立即把 store 里的 user 置空
          } else {
            // 兜底：store 尚未注册（例如首屏极早期就 401）。
            // 这里不强求同步，但要把跳转放在清理之后，
            // 且 Web 端用 replace 避免用户按返回键回到一个 401 页面。
            void import('@/store/authStore')
              .then(m => m.useAuthStore.setState({ user: null }))
              .catch(() => {
                /* 清理失败不影响跳转 */
              })
          }
        }
        redirectToLogin()
      }
      return Promise.reject({
        code: 401,
        msg: payload?.msg || '未登录',
        // 后端在 data.hint 里给出「该怎么带凭据」的说明，保留下来别丢
        data: payload?.data ?? null,
      } as IResponse)
    }

    // 后端已统一为 {code,msg,data}；detail 只是兜底，防止有路径绕过异常处理器
    const msg = payload?.msg || payload?.detail || '服务器错误，请稍后再试'
    if (!suppress) toast.error(msg)
    return Promise.reject({
      code: payload?.code ?? status ?? -1,
      msg,
      data: payload?.data ?? null,
    } as IResponse)
  },
)

export default request
