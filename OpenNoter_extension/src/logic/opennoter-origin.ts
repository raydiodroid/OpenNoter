/**
 * OpenNoter 页面来源校验（Cookie 一键同步的安全边界）。
 *
 * ## 这个文件防的是什么
 *
 * 浏览器扩展能读到用户的 B 站/抖音等站点的 Cookie。所以必须保证：
 * **只有用户自己的 OpenNoter 页面**才能请求扩展把 Cookie 交出来。
 *
 * 否则任何恶意网页只要发一条 runtime message 声称「我是 OpenNoter 设置页」，
 * 就能骗走用户的登录 Cookie —— 那是账号劫持。
 *
 * ## 为什么不能硬编码部署域名
 *
 * 早期实现把 `https://opennoter.<某个域名>` 写死在源码里。这有两个问题：
 *
 *   1. **泄漏部署者信息** —— 仓库是公开的，等于对外公布自己跑在哪个域名；
 *   2. **对别人不可用** —— 别人部署到自己的域名后，Cookie 同步会一律被拒
 *      （白名单里只有原作者的域名），功能等于坏的。
 *
 * ## 现在的做法
 *
 * 可信源 = **内置的本机地址** + **用户在扩展设置里填的后端地址**。
 *
 *   - 本机地址（localhost / 127.0.0.1）永远可信：开发与自建自用都是它；
 *   - 用户填了什么后端，就把那个 origin 加进可信集合 —— 这本来就是用户
 *     自己的选择，扩展信任用户配置的后端是合理的。
 *
 * 安全性没有削弱：攻击者仍无法让扩展信任**任意**域名，只能信任
 * 「用户在设置页里主动填过的那个」。
 */

/** 永远可信的本机前端地址（开发服务器默认端口）。 */
const LOCAL_ORIGINS = [
  'http://localhost:3015',
  'http://127.0.0.1:3015',
] as const

/** 把任意 URL 规范成 origin（失败返回 null）。 */
function toOrigin(url: string): string | null {
  try {
    return new URL(url).origin
  }
  catch {
    return null
  }
}

/**
 * 构造可信 OpenNoter origin 集合。
 *
 * @param configuredBackendUrl 用户在扩展设置里填的后端地址（可选）。
 *   后端与前端通常不同端口（后端 8483 / 前端 3015），这里同时接受后端
 *   origin —— 用户既然填了它，就是在告诉扩展「这是我自己的部署」。
 */
export function buildTrustedOrigins(configuredBackendUrl?: string | null): Set<string> {
  const origins = new Set<string>(LOCAL_ORIGINS)
  const backendOrigin = configuredBackendUrl ? toOrigin(configuredBackendUrl) : null
  if (backendOrigin)
    origins.add(backendOrigin)
  return origins
}

export function isTrustedOpenNoterOrigin(
  origin: string,
  configuredBackendUrl?: string | null,
): boolean {
  return buildTrustedOrigins(configuredBackendUrl).has(origin)
}

export function isValidCookieTransferContext({
  senderUrl,
  tabUrl,
  requestOrigin,
  platform,
  frameId,
  configuredBackendUrl,
}: {
  senderUrl: string
  tabUrl: string
  requestOrigin: string
  platform: string
  frameId?: number
  configuredBackendUrl?: string | null
}): boolean {
  // 只允许顶层 frame：iframe 里发起的请求一律拒绝
  if (frameId !== undefined && frameId !== 0)
    return false

  try {
    const sender = new URL(senderUrl)
    const tab = new URL(tabUrl)
    if (
      // 三个来源必须完全一致：防止「A 页面发消息、B 页面的 Cookie 被读走」
      sender.origin !== tab.origin
      || tab.origin !== requestOrigin
      || !isTrustedOpenNoterOrigin(tab.origin, configuredBackendUrl)
    )
      return false

    // 必须停在下载设置页（可带平台段），防止在任意页面滥用
    const route = tab.pathname.match(/^\/settings\/download(?:\/(bilibili|youtube|douyin|kuaishou))?\/?$/)
    const routePlatform = route?.[1] || (route ? 'bilibili' : null)
    return routePlatform === platform
  }
  catch {
    return false
  }
}
