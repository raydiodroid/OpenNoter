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
    ) {
      return
    }
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
      || !/^[\w-]{32,128}$/.test(message.transferId)
      || typeof message.platform !== 'string'
      || !COOKIE_PLATFORMS.has(message.platform as Exclude<Platform, 'local'>)
      || message.platform !== (downloadRoute[1] || 'bilibili')
    ) {
      return
    }

    const reply = (result: { ok: boolean, count?: number, error?: string }) => {
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
      }) as { ok: boolean, count?: number, error?: string }
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
  const shadowDOM = container.attachShadow?.({ mode: __DEV__ ? 'open' : 'closed' }) || container

  /**
   * 样式**内联**进 shadow DOM，不用 <link href="chrome-extension://...">。
   *
   * ## 为什么不能外链
   *
   * 在 shadow root 里放 `<link rel="stylesheet" href="chrome-extension://<id>/...">`，
   * Chrome 会拒绝加载并报：
   *
   *     Unsafe attempt to load URL chrome-extension://<id>/dist/contentScripts/style.css
   *     from frame with URL https://www.youtube.com/. Domains, protocols and ports
   *     must match.
   *
   * 即使 style.css 已列进 `web_accessible_resources` 且 matches 覆盖了该站点，
   * shadow root 内的资源加载仍要走发起方的来源校验，跨协议（https vs
   * chrome-extension）直接失败。结果是按钮渲染出来但**完全没有样式**。
   *
   * 改成运行时 fetch 再塞成 <style>：请求由 content script 发出（有扩展权限），
   * 拿到文本后内联，shadow DOM 就不再有跨源引用。
   *
   * 失败时不阻塞 UI —— 无样式的按钮仍然可点，只是难看；总比整个悬浮层不出现好。
   */
  const applyStyles = async () => {
    try {
      const url = browser.runtime.getURL('dist/contentScripts/style.css')
      const css = await fetch(url).then(r => r.text())
      const styleEl = document.createElement('style')
      styleEl.textContent = css
      shadowDOM.insertBefore(styleEl, root)
    }
    catch (e) {
      console.warn('[OpenNoter] 悬浮按钮样式加载失败（不影响功能）:', e)
    }
  }

  shadowDOM.appendChild(root)
  document.body.appendChild(container)
  void applyStyles()

  const app = createApp(App)
  setupApp(app)
  app.mount(root)
})()

installCookieTransferBridge()
