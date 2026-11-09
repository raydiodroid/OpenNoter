import { describe, expect, it } from 'vitest'
import { buildTrustedOrigins, isValidCookieTransferContext } from '../logic/opennoter-origin'
import { toNetscapeFile, toNetscapeLine } from '../logic/netscape'

/** 测试用示例域名（不是任何真实部署）。 */
const SELF_HOST = 'https://notes.example.com'
const BACKEND = 'http://localhost:8483'

describe('buildTrustedOrigins', () => {
  it('always trusts localhost development origins', () => {
    const origins = buildTrustedOrigins()
    expect(origins.has('http://localhost:3015')).toBe(true)
    expect(origins.has('http://127.0.0.1:3015')).toBe(true)
  })

  it('adds the user-configured backend origin', () => {
    const origins = buildTrustedOrigins('https://my-notes.example.org')
    expect(origins.has('https://my-notes.example.org')).toBe(true)
  })

  it('ignores an invalid configured URL instead of throwing', () => {
    expect(() => buildTrustedOrigins('not a url')).not.toThrow()
    expect(buildTrustedOrigins('not a url').has('not a url')).toBe(false)
  })
})

describe('isValidCookieTransferContext', () => {
  const base = {
    senderUrl: `${SELF_HOST}/settings/download/bilibili`,
    tabUrl: `${SELF_HOST}/settings/download/bilibili`,
    requestOrigin: SELF_HOST,
    platform: 'bilibili',
    frameId: 0,
    configuredBackendUrl: SELF_HOST,
  }

  it('allows same-origin SPA navigation to a downloader route when frameId is omitted', () => {
    expect(isValidCookieTransferContext({
      senderUrl: `${SELF_HOST}/settings/ai`,
      tabUrl: `${SELF_HOST}/settings/download/bilibili`,
      requestOrigin: SELF_HOST,
      platform: 'bilibili',
      configuredBackendUrl: SELF_HOST,
    })).toBe(true)
  })

  it('allows the extension root downloader route as Bilibili', () => {
    expect(isValidCookieTransferContext({
      ...base,
      senderUrl: `${SELF_HOST}/settings/download`,
      tabUrl: `${SELF_HOST}/settings/download`,
    })).toBe(true)
  })

  it('rejects untrusted origins, iframes, and mismatched platform routes', () => {
    // 关键安全断言：即使对方域名合法，只要不是用户配置的那个就拒绝
    expect(isValidCookieTransferContext({
      ...base,
      senderUrl: 'https://attacker.example/settings/download/bilibili',
      tabUrl: 'https://attacker.example/settings/download/bilibili',
      requestOrigin: 'https://attacker.example',
    })).toBe(false)
    expect(isValidCookieTransferContext({ ...base, frameId: 3 })).toBe(false)
    expect(isValidCookieTransferContext({ ...base, platform: 'douyin' })).toBe(false)
  })

  it('rejects when the user has not configured this deployment', () => {
    // 未配置任何自定义后端时，只有本机地址可信
    expect(isValidCookieTransferContext({
      senderUrl: `${SELF_HOST}/settings/download/bilibili`,
      tabUrl: `${SELF_HOST}/settings/download/bilibili`,
      requestOrigin: SELF_HOST,
      platform: 'bilibili',
      frameId: 0,
    })).toBe(false)
  })

  it('allows localhost deployment without extra configuration', () => {
    expect(isValidCookieTransferContext({
      senderUrl: 'http://localhost:3015/settings/download/bilibili',
      tabUrl: 'http://localhost:3015/settings/download/bilibili',
      requestOrigin: 'http://localhost:3015',
      platform: 'bilibili',
      frameId: 0,
      configuredBackendUrl: BACKEND,
    })).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// YouTube 的 Netscape 序列化
// ---------------------------------------------------------------------------
//
// 背景：扩展原先 `COOKIE_NAME_ALLOWLISTS.youtube = []` 且 `readBrowserCookies`
// 在空 allowlist 时直接返回空串 —— 浏览器一键同步对 YouTube 完全失效。
//
// 但 YouTube 又**必须**用 Cookie（机房出口 IP 会被风控要求登录验证），
// 所以这里补上 Netscape 导出。格式不能错：yt-dlp 只认 7 字段 tab 分隔的
// cookies.txt，传 `name=value; ...` 请求头形式会被后端 normalize 拒绝。

describe('toNetscapeLine', () => {
  const base = {
    domain: '.youtube.com',
    httpOnly: false,
    secure: true,
    path: '/',
    name: 'SID',
    value: 'abc123',
  } as any

  it('produces exactly 7 tab-separated fields', () => {
    const line = toNetscapeLine(base)
    expect(line.split('\t')).toHaveLength(7)
  })

  it('marks includeSubdomains TRUE only for dot-prefixed domains', () => {
    expect(toNetscapeLine({ ...base, domain: '.youtube.com' }).split('\t')[1]).toBe('TRUE')
    expect(toNetscapeLine({ ...base, domain: 'youtube.com' }).split('\t')[1]).toBe('FALSE')
  })

  it('prefixes HttpOnly cookies with #HttpOnly_', () => {
    const line = toNetscapeLine({ ...base, httpOnly: true })
    expect(line.startsWith('#HttpOnly_.youtube.com')).toBe(true)
  })

  it('floors fractional expirationDate and writes 0 for session cookies', () => {
    expect(toNetscapeLine({ ...base, expirationDate: 1800000000.7 }).split('\t')[4])
      .toBe('1800000000')
    expect(toNetscapeLine({ ...base, expirationDate: undefined }).split('\t')[4]).toBe('0')
  })
})

describe('toNetscapeFile', () => {
  it('returns empty string for no cookies (so caller can detect "nothing to sync")', () => {
    expect(toNetscapeFile([])).toBe('')
  })

  it('emits the standard header line first', () => {
    const out = toNetscapeFile([
      { domain: '.youtube.com', name: 'SID', value: 'x', secure: true },
    ] as any)
    expect(out.split('\n')[0]).toBe('# Netscape HTTP Cookie File')
  })

  it('round-trips multiple cookies as separate data lines', () => {
    const out = toNetscapeFile([
      { domain: '.youtube.com', name: 'SID', value: 'a', secure: true },
      { domain: '.youtube.com', name: 'PREF', value: 'b', httpOnly: true },
    ] as any)
    const dataLines = out.split('\n').filter(l => l.trim() && !l.startsWith('# '))
    expect(dataLines).toHaveLength(2)
    expect(dataLines[1].startsWith('#HttpOnly_')).toBe(true)
  })
})
