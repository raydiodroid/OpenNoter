/**
 * 零依赖的 URL 纯函数。
 *
 * ## 为什么单独一个文件（不是顺手重构）
 *
 * `lib/api.ts` 的依赖链是 `api.ts → lib/toast.ts → @react-spectrum/s2`，
 * 而 `@react-spectrum/s2` 依赖 **parcel 宏**（`style(...) from '...' with { type: 'macro' }`），
 * 这个宏只在 Vite/parcel 构建期展开，**在 node 里 import 会直接失败**。
 *
 * 结果就是：凡是写在 `api.ts` 里的纯函数都没法被单元测试覆盖。而这些恰恰是
 * 最需要回归保护的部分（静态资源地址拼接、鉴权头、401 跳转）。
 *
 * 所以把纯函数抽到这里（零依赖），`api.ts` 用 `export *` 原样回导，
 * **所有既有调用点一行都不用改**，同时这些函数可以被 node/Vitest 直接测。
 */

/**
 * 后端 API 基础地址。
 *
 * - Web（nginx / Vite 代理同源）：默认 `/api`
 * - Tauri（`VITE_PLATFORM=tauri`）：`.env.tauri` 里是 `http://127.0.0.1:8483/api`
 *
 * 注意：**不要**把 `.env.example` 里那种不带 `/api` 的绝对地址烘进 bundle。
 *
 * `import.meta.env` 用可选链取值：Vite 构建时会把它整块静态替换成对象，
 * 但在**裸 node**（想直接 import 本文件做单测）里 `import.meta.env` 是
 * `undefined`，直接 `.VITE_API_BASE_URL` 会抛 `TypeError` —— 那样「零依赖、
 * 可单测」就成了空话。可选链让它在两种环境下都安全，回退值仍是 `/api`。
 */
export const API_BASE = String(import.meta.env?.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

/** 是否运行在 Tauri 桌面端 */
export const isTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/**
 * 拼接后端 API 地址（用于接口路径，会自动加 `/api` 前辍由 baseURL 处理，
 * 这里只用于「需要完整 URL 字符串」的场景，如拼静态资源、OIDC 跳转）。
 */
export const apiUrl = (path: string) => `${API_BASE}${path}`

/**
 * 后端 origin（含 scheme+host+port），不含 `/api` 后缀。
 *
 * Tauri 里 `API_BASE` 是绝对地址（`http://127.0.0.1:8483/api`），
 * 所以去掉末尾的 `/api` 就得到 sidecar 的 origin。
 * Web 的 `API_BASE` 是相对路径（`/api`），此时没有独立 origin，返回空串
 * —— 调用方应理解为「同源」。
 */
export function apiOrigin(): string {
  const base = API_BASE
  if (!/^https?:\/\//i.test(base)) return ''
  return base.replace(/\/api\/?$/i, '').replace(/\/$/, '')
}

/**
 * 把后端返回的 `/static/...` 相对路径变成「当前端能直接用的地址」。
 *
 * ## 为什么需要（F9）
 *
 * Tauri 的页面源是 `tauri://localhost`，若原样用 `/static/...`，
 * 浏览器会解析成 `tauri://localhost/static/...` —— 这个地址根本没有文件服务，
 * 必然 404。所以桌面端必须把 `/static/` 前缀换成 sidecar 的 origin。
 *
 * **关键约束：query 必须原样透传。**
 * 截图 URL 上可能带 per-task 长期令牌（`?t=<token>`，见后端
 * `app/utils/static_signing.py`）。`<img>` 是裸 GET，带不上 Cookie/Authorization，
 * 令牌就是唯一的凭据。任何「用 `new URL()` 再重建 query」的写法都可能丢参数
 * 或改变编码，导致令牌失效 → 图片 403。
 */
export function staticUrl(path: string): string {
  if (!path) return ''

  // 已经是绝对地址（http/https/data/blob）→ 原样返回。
  // 历史数据里可能存在 `http://localhost:8483/static/...` 这种旧封面地址，
  // 也走这条分支（由 resolveCoverUrl 决定是否改用代理）。
  if (/^(https?:|data:|blob:)/i.test(path)) return path

  const origin = apiOrigin()
  if (!origin) return path // Web：同源相对路径即可

  // 只做前缀替换，**不碰 query / hash**。
  if (path.startsWith('/')) return `${origin}${path}`
  return `${origin}/${path}`
}

/**
 * 判断一个地址是不是「后端自己产的 `/static/` 资源」，是则返回**站内路径**
 * （`pathname + search`，保留 query 上的凭据参数），否则返回 null。
 *
 * 用于封面地址分流（X1）：后端自己产的本地视频封面必须**直连**，
 * 不能丢给 `/api/image_proxy` —— image_proxy 会做 SSRF 校验，先拦 loopback、
 * 再查图片 CDN 白名单（不含 localhost），于是本地视频封面恒定 400。
 *
 * ## 判据为什么是「loopback OR 同源」
 *
 * 早期草案只判断 loopback（`localhost` / `127.0.0.1`），会漏掉两类部署：
 *   - 用户把后端地址配成内网域名（如 `http://my-host:8483`）；
 *   - Tauri / 开发环境：页面在 `tauri://localhost` 或 `localhost:3015`，
 *     后端在 `127.0.0.1:8483` —— 只判同源又会漏掉。
 *
 * 所以两者取并集。同源判定用 `host`（含端口）而非 `origin`：反代可能把
 * scheme 改写（页面 https、后端 http），不能因为协议不同就判成外部地址。
 */
export function ownStaticPath(raw: string): string | null {
  const url = (raw || '').trim()
  if (!url) return null

  // 新形态：后端修好后直接返回站内相对路径
  if (url.startsWith('/static/')) return url

  if (!/^https?:\/\//i.test(url)) return null

  try {
    const parsed = new URL(url)
    if (!parsed.pathname.startsWith('/static/')) return null
    const host = parsed.hostname.toLowerCase()
    const isLoopback = ['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'].includes(host)
    const sameHost = typeof window !== 'undefined' && parsed.host === window.location.host
    return isLoopback || sameHost ? `${parsed.pathname}${parsed.search}` : null
  } catch {
    return null
  }
}

/** 布尔版判据：是否指向「自己的后端静态资源」 */
export function isOwnBackendStaticUrl(raw: string): boolean {
  return ownStaticPath(raw) !== null
}

/**
 * 封面地址解析：决定「直连」还是「走 image_proxy」。
 *
 * - 后端自己的 `/static/` 资源 → 直连（`staticUrl`）
 * - 其它站内相对路径（历史遗留上传路径）→ 按 API 前缀拼
 * - 第三方外链（B站/YouTube/抖音/快手的封面）→ 走 `/api/image_proxy` 代理，
 *   避免跨域与 Referer 限制
 *
 * **绝不能**把后端自产的 `/static/...` 丢进 `/api/image_proxy`：那条路会走
 * `assert_safe_url`，先拦 loopback、再查 CDN 白名单（不含 localhost）⇒ 400。
 * 这正是「本地视频封面必挂」（X1）的成因。
 */
export function resolveCoverUrl(url?: string | null): string {
  const raw = (url || '').trim()
  if (!raw) return ''

  const own = ownStaticPath(raw)
  if (own) return staticUrl(own)

  // 其它站内相对路径（历史遗留的上传/截图路径）：仍按 API 前缀拼
  if (raw.startsWith('/')) return apiUrl(raw)

  // 第三方图片：走后端代理，绕开防盗链与跨域
  return apiUrl(`/image_proxy?url=${encodeURIComponent(raw)}`)
}

/**
 * 桌面端 OIDC 登录入口的地址。
 *
 * Tauri webview 里 `window.location.href = '/api/auth/oidc/login'` 会解析到
 * `tauri://localhost/api/...`（没有这个服务），必须用 sidecar 的绝对地址。
 * Web 端保持同源相对路径，让浏览器正常跟随 302 到 IdP。
 */
export function oidcLoginUrl(): string {
  const origin = apiOrigin()
  if (origin) return `${origin}/api/auth/oidc/login`
  return '/api/auth/oidc/login'
}
