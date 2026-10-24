import axios, { AxiosInstance, AxiosResponse } from 'axios'
import { toast } from '@/lib/toast'

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

export const API_BASE = String(import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

/** 拼接后端静态资源地址（封面代理、上传文件等） */
export const apiUrl = (path: string) => `${API_BASE}${path}`

const request: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
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
      if (enforced && !isAuthProbe && !window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
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
