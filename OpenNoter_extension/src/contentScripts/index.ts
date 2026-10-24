import { createApp } from 'vue'
import App from './views/App.vue'
import { setupApp } from '~/logic/common-setup'
import { detectPlatform } from '~/logic/platform'
import { isTrustedOpenNoterOrigin } from '~/logic/opennoter-origin'
import type { Platform } from '~/logic/types'

const COOKIE_PLATFORMS = new Set<Exclude<Platform, 'local'>>([
  'bilibili',
  'youtube',
  'douyin',
  'kuaishou',
])

function installCookieTransferBridge() {
  window.addEventListener('message', async (event: MessageEvent) => {
    if (
      event.source !== window
      || event.origin !== window.location.origin
      || !isTrustedOpenNoterOrigin(event.origin)
    )
      return
    const downloadRoute = window.location.pathname.match(/^\/settings\/download(?:\/(bilibili|youtube|douyin|kuaishou))?\/?$/)
    if (!downloadRoute)
      return

    const message = event.data as Record<string, unknown> | null
    if (
      !message
      || message.source !== 'opennoter-web'
      || message.type !== 'cookie-transfer-request'
      || typeof message.requestId !== 'string'
      || message.requestId.length > 100
      || typeof message.transferId !== 'string'
      || !/^[A-Za-z0-9_-]{32,128}$/.test(message.transferId)
      || typeof message.platform !== 'string'
      || !COOKIE_PLATFORMS.has(message.platform as Exclude<Platform, 'local'>)
      || message.platform !== (downloadRoute[1] || 'bilibili')
    )
      return

    const reply = (result: { ok: boolean; count?: number; error?: string }) => {
      window.postMessage({
        source: 'opennoter-extension',
        type: 'cookie-transfer-result',
        requestId: message.requestId,
        ...result,
      }, event.origin)
    }

    try {
      const result = await browser.runtime.sendMessage({
        type: 'opennoter-cookie-transfer',
        requestId: message.requestId,
        transferId: message.transferId,
        platform: message.platform,
        pageOrigin: event.origin,
      }) as { ok: boolean; count?: number; error?: string }
      reply(result)
    }
    catch {
      reply({ ok: false, error: '无法连接浏览器扩展后台，请重启或重新加载扩展' })
    }
  })
}

// 只在支持的视频平台上挂悬浮按钮，避免污染其他网站
(() => {
  if (!detectPlatform(window.location.href))
    return

  const container = document.createElement('div')
  container.id = __NAME__
  const root = document.createElement('div')
  const styleEl = document.createElement('link')
  const shadowDOM = container.attachShadow?.({ mode: __DEV__ ? 'open' : 'closed' }) || container
  styleEl.setAttribute('rel', 'stylesheet')
  styleEl.setAttribute('href', browser.runtime.getURL('dist/contentScripts/style.css'))
  shadowDOM.appendChild(styleEl)
  shadowDOM.appendChild(root)
  document.body.appendChild(container)
  const app = createApp(App)
  setupApp(app)
  app.mount(root)
})()

installCookieTransferBridge()
