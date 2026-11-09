/**
 * Netscape cookies.txt 序列化（**零依赖纯函数**）。
 *
 * ## 为什么单独一个文件
 *
 * `logic/cookies.ts` 用了自动导入的 `browser`（webextension-polyfill），
 * 在 node 环境（vitest）里 import 会抛 "This script should only be loaded in
 * a browser extension"。把纯函数拆出来才能单测——与 `lib/urls.ts` 从
 * `lib/api.ts` 拆出去的理由相同。
 *
 * ## 格式
 *
 * 7 个 tab 分隔字段：
 *   `domain  includeSubdomains  path  secure  expiry  name  value`
 *
 * yt-dlp 只认这个格式；传 `name=value; ...` 请求头形式会被后端
 * `normalize_netscape_cookies` 拒绝（字段数不对直接报错，不做容错——
 * 猜错会让 yt-dlp 静默读不到）。
 */

/** webextension-polyfill 的 Cookie 形状（只列用到的字段，便于测试构造） */
export interface NetscapeCookieLike {
  domain: string
  httpOnly?: boolean
  secure?: boolean
  path?: string
  expirationDate?: number
  name: string
  value: string
}

/** 序列化成 cookies.txt 的一行。 */
export function toNetscapeLine(cookie: NetscapeCookieLike): string {
  // HttpOnly 的 cookie 必须在 domain 前加前缀，否则 yt-dlp 不认
  const domainPart = cookie.httpOnly ? `#HttpOnly_${cookie.domain}` : cookie.domain
  const includeSub = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE'
  const path = cookie.path || '/'
  const secure = cookie.secure ? 'TRUE' : 'FALSE'
  const expiry = cookie.expirationDate ? Math.floor(cookie.expirationDate) : 0
  return [domainPart, includeSub, path, secure, String(expiry), cookie.name, cookie.value]
    .join('\t')
}

/** 把一组 cookie 序列化成完整的 cookies.txt 文本。 */
export function toNetscapeFile(cookies: readonly NetscapeCookieLike[]): string {
  if (!cookies.length)
    return ''
  const lines = ['# Netscape HTTP Cookie File']
  for (const cookie of cookies)
    lines.push(toNetscapeLine(cookie))
  return `${lines.join('\n')}\n`
}
