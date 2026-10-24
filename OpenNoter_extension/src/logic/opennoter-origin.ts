const TRUSTED_OPENNOTER_ORIGINS = new Set([
  'https://notes.example.com',
  'http://localhost:3015',
  'http://127.0.0.1:3015',
])

export function isTrustedOpenNoterOrigin(origin: string): boolean {
  return TRUSTED_OPENNOTER_ORIGINS.has(origin)
}

export function isValidCookieTransferContext({
  senderUrl,
  tabUrl,
  requestOrigin,
  platform,
  frameId,
}: {
  senderUrl: string
  tabUrl: string
  requestOrigin: string
  platform: string
  frameId?: number
}): boolean {
  if (frameId !== undefined && frameId !== 0)
    return false

  try {
    const sender = new URL(senderUrl)
    const tab = new URL(tabUrl)
    if (
      sender.origin !== tab.origin
      || tab.origin !== requestOrigin
      || !isTrustedOpenNoterOrigin(tab.origin)
    )
      return false

    const route = tab.pathname.match(/^\/settings\/download(?:\/(bilibili|youtube|douyin|kuaishou))?\/?$/)
    const routePlatform = route?.[1] || (route ? 'bilibili' : null)
    return routePlatform === platform
  }
  catch {
    return false
  }
}
