import type { Platform } from './types'
import { toNetscapeFile } from './netscape'

// 两种 Cookie 格式，按平台区分（**不能混用**）：
//
//   - B 站 / 抖音 / 快手：`name=value; name=value; ...` 请求头形式。
//     下载器直接把整串塞进 `Cookie:` 头（见 bilibili_downloader.py 的 split("; ")）。
//
//   - YouTube：**Netscape cookies.txt** 形式（7 个 tab 分隔字段）。
//     yt-dlp 只认 `--cookies` 文件，不认请求头；后端 cookie_policy 里
//     `COOKIE_FORMATS.youtube = "netscape"`，传请求头形式会被 normalize 拒绝
//     （字段数不对直接报错，不做容错——猜错会让 yt-dlp 静默读不到）。
const COOKIE_DOMAINS: Record<Exclude<Platform, 'local'>, string> = {
  bilibili: '.bilibili.com',
  youtube: '.youtube.com',
  douyin: '.douyin.com',
  kuaishou: '.kuaishou.com',
}

export const COOKIE_NAME_ALLOWLISTS: Record<Exclude<Platform, 'local'>, readonly string[]> = {
  bilibili: ['SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5', 'sid', 'buvid3', 'buvid4'],
  douyin: [
    'sessionid',
    'sessionid_ss',
    'sid_guard',
    'sid_tt',
    'uid_tt',
    'uid_tt_ss',
    'passport_csrf_token',
    'passport_csrf_token_default',
    'sid_ucp_v1',
    'ssid_ucp_v1',
    'ttwid',
    'odin_tt',
  ],
  kuaishou: ['did', 'didv', 'kpf', 'kpn', 'clientid', 'userId', 'kuaishou.server.web_st', 'kuaishou.server.web_ph'],
  youtube: [],
}

export const SUPPORTED_COOKIE_PLATFORMS: Array<Exclude<Platform, 'local'>> = [
  'bilibili',
  'douyin',
  'kuaishou',
  'youtube',
]

export async function readBrowserCookies(platform: Exclude<Platform, 'local'>): Promise<string> {
  const domain = COOKIE_DOMAINS[platform]
  const list = await browser.cookies.getAll({ domain })

  // YouTube：Netscape 全量导出（不按名字过滤——yt-dlp 需要完整登录态，
  // 且它自己会挑需要的字段；按 allowlist 过滤反而可能漏掉关键 cookie）
  if (platform === 'youtube') {
    // 全量导出（不按名字过滤）——yt-dlp 需要完整登录态，按 allowlist 过滤
    // 反而可能漏掉关键 cookie
    return toNetscapeFile(list)
  }

  // 其余三个平台：请求头形式 + 名字白名单（只带下载器需要的登录项）
  const allowed = new Set(COOKIE_NAME_ALLOWLISTS[platform])
  if (!allowed.size)
    return ''
  const selected = new Map<string, string>()
  for (const cookie of list) {
    if (allowed.has(cookie.name))
      selected.set(cookie.name, cookie.value)
  }
  return COOKIE_NAME_ALLOWLISTS[platform]
    .filter(name => selected.has(name))
    .map(name => `${name}=${selected.get(name)}`)
    .join('; ')
}

export async function syncCookieToBackend(platform: Exclude<Platform, 'local'>): Promise<{ ok: boolean, count: number, error?: string }> {
  try {
    const cookieStr = await readBrowserCookies(platform)
    if (!cookieStr) {
      // 所有四个平台现在都支持同步；空结果只可能是「浏览器里没有该站 Cookie」
      return {
        ok: false,
        count: 0,
        error: '当前浏览器没有该平台的 Cookie，请先登录目标站点后再同步',
      }
    }
    const count = cookieStr.split('; ').length
    const { setDownloaderCookie } = await import('./api')
    await setDownloaderCookie(platform, cookieStr)
    return { ok: true, count }
  }
  catch (e) {
    return { ok: false, count: 0, error: (e as Error).message }
  }
}
