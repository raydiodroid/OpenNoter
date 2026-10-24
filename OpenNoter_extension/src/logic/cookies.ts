import type { Platform } from './types'

// 后端期望的 cookie 字符串格式：name=value; name=value; ...
// 见 backend/app/downloaders/bilibili_downloader.py 的 split("; ")
const COOKIE_DOMAINS: Record<Exclude<Platform, 'local'>, string> = {
  bilibili: '.bilibili.com',
  youtube: '.youtube.com',
  douyin: '.douyin.com',
  kuaishou: '.kuaishou.com',
}

export const COOKIE_NAME_ALLOWLISTS: Record<Exclude<Platform, 'local'>, readonly string[]> = {
  bilibili: ['SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5', 'sid', 'buvid3', 'buvid4'],
  douyin: [
    'sessionid', 'sessionid_ss', 'sid_guard', 'sid_tt', 'uid_tt', 'uid_tt_ss',
    'passport_csrf_token', 'passport_csrf_token_default', 'sid_ucp_v1', 'ssid_ucp_v1', 'ttwid', 'odin_tt',
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
  const allowed = new Set(COOKIE_NAME_ALLOWLISTS[platform])
  if (!allowed.size)
    return ''
  const domain = COOKIE_DOMAINS[platform]
  const list = await browser.cookies.getAll({ domain })
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
      const error = COOKIE_NAME_ALLOWLISTS[platform].length
        ? '当前浏览器没有该平台下载器需要的 Cookie，请先登录目标站点'
        : '该平台的下载器当前不使用 Cookie，无需同步'
      return { ok: false, count: 0, error }
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
