import { describe, expect, it } from 'vitest'
import { buildTrustedOrigins, isValidCookieTransferContext } from '../logic/opennoter-origin'

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
