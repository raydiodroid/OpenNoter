import { describe, expect, it } from 'vitest'
import { isValidCookieTransferContext } from '../logic/opennoter-origin'

describe('isValidCookieTransferContext', () => {
  it('allows same-origin SPA navigation to a downloader route when frameId is omitted', () => {
    expect(isValidCookieTransferContext({
      senderUrl: 'https://opennoter.raydiodroid.top/settings/ai',
      tabUrl: 'https://opennoter.raydiodroid.top/settings/download/bilibili',
      requestOrigin: 'https://opennoter.raydiodroid.top',
      platform: 'bilibili',
    })).toBe(true)
  })

  it('allows the extension root downloader route as Bilibili', () => {
    expect(isValidCookieTransferContext({
      senderUrl: 'https://opennoter.raydiodroid.top/settings/download',
      tabUrl: 'https://opennoter.raydiodroid.top/settings/download',
      requestOrigin: 'https://opennoter.raydiodroid.top',
      platform: 'bilibili',
      frameId: 0,
    })).toBe(true)
  })

  it('rejects untrusted origins, iframes, and mismatched platform routes', () => {
    const base = {
      senderUrl: 'https://opennoter.raydiodroid.top/settings/download/bilibili',
      tabUrl: 'https://opennoter.raydiodroid.top/settings/download/bilibili',
      requestOrigin: 'https://opennoter.raydiodroid.top',
      platform: 'bilibili',
      frameId: 0,
    }
    expect(isValidCookieTransferContext({ ...base, senderUrl: 'https://attacker.example/settings/download/bilibili' })).toBe(false)
    expect(isValidCookieTransferContext({ ...base, frameId: 3 })).toBe(false)
    expect(isValidCookieTransferContext({ ...base, platform: 'douyin' })).toBe(false)
  })
})
