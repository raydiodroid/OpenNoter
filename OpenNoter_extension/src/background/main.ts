import { onMessage } from 'webext-bridge/background'
import type { Platform, Settings, TaskRecord } from '~/logic/types'
import { DEFAULT_SETTINGS, MAX_TASKS, SETTINGS_KEY, TASKS_KEY } from '~/logic/constants'
import { detectPlatform } from '~/logic/platform'
import { authHeaders } from '~/logic/api'
import { fetchBilibiliSubtitle } from '~/logic/bilibili-subtitle'
import { normalizeVideoTitle } from '~/logic/task-display'
import { COOKIE_NAME_ALLOWLISTS, readBrowserCookies, SUPPORTED_COOKIE_PLATFORMS } from '~/logic/cookies'
import { isValidCookieTransferContext } from '~/logic/opennoter-origin'
import type { Runtime } from 'webextension-polyfill'

type WebExtMessageSender = Runtime.MessageSender

// only on dev mode
if (import.meta.hot) {
  // @ts-expect-error for background HMR
  import('/@vite/client')
  // load latest content script
  import('./contentScriptHMR')
}

// ---------- 直接操作 chrome.storage（service worker 里别用 Vue 反应式）----------

async function readSettings(): Promise<Settings> {
  const obj = await browser.storage.local.get(SETTINGS_KEY)
  const raw = obj[SETTINGS_KEY] as string | undefined
  if (!raw)
    return { ...DEFAULT_SETTINGS }
  try {
    return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) }
  }
  catch {
    return { ...DEFAULT_SETTINGS }
  }
}

async function readTasks(): Promise<TaskRecord[]> {
  const obj = await browser.storage.local.get(TASKS_KEY)
  const raw = obj[TASKS_KEY] as string | undefined
  if (!raw)
    return []
  try {
    return JSON.parse(raw) as TaskRecord[]
  }
  catch {
    return []
  }
}

async function writeTasks(tasks: TaskRecord[]) {
  await browser.storage.local.set({ [TASKS_KEY]: JSON.stringify(tasks.slice(0, MAX_TASKS)) })
}

async function upsertTask(record: TaskRecord) {
  const tasks = await readTasks()
  const idx = tasks.findIndex(t => t.taskId === record.taskId)
  if (idx >= 0)
    tasks.splice(idx, 1, { ...tasks[idx], ...record })
  else
    tasks.unshift(record)
  await writeTasks(tasks)
}

// ---------- 启动任务 ----------

async function startTask(url: string, title?: string): Promise<{ ok: boolean, taskId?: string, error?: string }> {
  const platform = detectPlatform(url)
  const displayTitle = normalizeVideoTitle(title)
  if (!platform)
    return { ok: false, error: '当前链接不是支持的视频平台' }

  const settings = await readSettings()
  if (!settings.providerId || !settings.modelName)
    return { ok: false, error: '请先在设置页选择供应商与模型' }

  const backend = settings.backendUrl.replace(/\/$/, '')

  // B 站：先在浏览器里抓字幕（带本地登录态 cookie），随提交带过去
  const prefetched = platform === 'bilibili' ? await fetchBilibiliSubtitle(url) : null

  const formats = settings.formats || []
  try {
    const res = await fetch(`${backend}/api/generate_note`, {
      method: 'POST',
      // F3：后台 `fetch` 不在 logic/api.ts 的 `request()` 覆盖范围内，
      // 必须显式复用同一套认证头。否则默认 AUTH_ENFORCE=true 的后端会稳定 401，
      // 而弹窗（走 request）却正常 —— 表现为「右键菜单时好时坏」。
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        video_url: url,
        platform,
        quality: settings.quality,
        provider_id: settings.providerId,
        model_name: settings.modelName,
        // backend 同时接受 format 数组与 screenshot/link 单独布尔；从 formats 派生保持单一真相源
        format: [...formats],
        screenshot: formats.includes('screenshot'),
        link: formats.includes('link'),
        style: settings.style || undefined,
        extras: settings.extras || undefined,
        video_understanding: settings.video_understanding || undefined,
        video_interval: settings.video_understanding ? settings.video_interval : undefined,
        grid_size: settings.video_understanding ? settings.grid_size : undefined,
        prefetched_transcript: prefetched ?? undefined,
      }),
    })
    if (!res.ok)
      return { ok: false, error: `HTTP ${res.status}` }
    const body = await res.json() as { code: number, msg: string, data: { task_id: string } }
    if (body.code !== 0)
      return { ok: false, error: body.msg }

    await upsertTask({
      taskId: body.data.task_id,
      videoUrl: url,
      platform,
      status: 'PENDING',
      message: '已提交',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      title: displayTitle,
    })
    return { ok: true, taskId: body.data.task_id }
  }
  catch (e) {
    return { ok: false, error: (e as Error).message }
  }
}

async function openSidePanelInTab(tabId?: number) {
  try {
    // @ts-expect-error chrome.sidePanel 类型在 webextension-polyfill 中尚未补全
    if (typeof chrome !== 'undefined' && chrome.sidePanel?.open && tabId !== undefined)
      // @ts-expect-error see above
      await chrome.sidePanel.open({ tabId })
  }
  catch (err) {
    console.warn('打开侧边栏失败：', err)
  }
}

// ---------- 消息桥 ----------

onMessage<{ url: string; title?: string }, 'opennoter-start'>('opennoter-start', async ({ data, sender }) => {
  const result = await startTask(data.url, data.title)
  // 成功就把侧边栏拉起来给用户看进度
  if (result.ok)
    await openSidePanelInTab(sender?.tabId)
  return result
})

// 这里必须显式标注参数类型：polyfill 的 OnMessageListener 是个联合类型，
// 省略注解在 strict 模式下会被推成 any 并报 TS7006（`tsc --noEmit` 会挂）。
browser.runtime.onMessage.addListener(async (message: unknown, sender: WebExtMessageSender) => {
  if (!message || typeof message !== 'object' || (message as { type?: string }).type !== 'opennoter-cookie-transfer')
    return undefined

  const request = message as {
    requestId?: unknown
    transferId?: unknown
    platform?: unknown
    pageOrigin?: unknown
  }
  const fail = (error: string) => ({ ok: false, error })
  if (
    typeof request.requestId !== 'string'
    || request.requestId.length > 100
    || typeof request.transferId !== 'string'
    || !/^[A-Za-z0-9_-]{32,128}$/.test(request.transferId)
    || typeof request.platform !== 'string'
    || !(SUPPORTED_COOKIE_PLATFORMS as string[]).includes(request.platform)
    || typeof request.pageOrigin !== 'string'
  )
    return fail('浏览器 Cookie 请求无效')

  if (typeof sender.tab?.id !== 'number')
    return fail('请从 OpenNoter 设置页发起 Cookie 获取')

  try {
    const tab = await browser.tabs.get(sender.tab.id)
    const senderUrl = sender.url || tab.url
    if (!senderUrl || !tab.url)
      return fail('无法确认当前 OpenNoter 页面')
    if (!isValidCookieTransferContext({
      senderUrl,
      tabUrl: tab.url,
      requestOrigin: request.pageOrigin,
      platform: request.platform,
      frameId: sender.frameId,
    }))
      return fail('Cookie 获取仅允许从 OpenNoter 下载设置页发起')

    const tabUrl = new URL(tab.url)
    const platform = request.platform as Exclude<Platform, 'local'>
    const cookie = await readBrowserCookies(platform)
    if (!cookie) {
      const error = COOKIE_NAME_ALLOWLISTS[platform].length
        ? '当前浏览器没有该平台下载器需要的 Cookie，请先登录对应网站'
        : '该平台的下载器当前不使用 Cookie，无需同步'
      return fail(error)
    }
    if (cookie.length > 32768)
      return fail('Cookie 数据过大，请改用扫码登录或手动设置')

    const response = await fetch(new URL('/api/complete_browser_cookie_transfer', tabUrl.origin), {
      method: 'POST',
      mode: 'cors',
      credentials: 'omit',
      redirect: 'error',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transfer_id: request.transferId,
        page_origin: tabUrl.origin,
        cookie,
      }),
    })
    const body = await response.json() as {
      code?: number
      msg?: string
      detail?: string
      data?: { count?: number }
    }
    if (!response.ok)
      return fail(body.detail || body.msg || `Cookie 保存失败 (HTTP ${response.status})`)
    if (body.code !== 0)
      return fail(body.msg || '后端拒绝保存 Cookie')
    return { ok: true, count: body.data?.count ?? cookie.split('; ').length }
  }
  catch (error) {
    return fail((error as Error).message || 'Cookie 同步失败')
  }
})

// ---------- 安装时事件 ----------

browser.runtime.onInstalled.addListener(() => {
  console.log('OpenNoter extension installed')

  // 右键菜单：在视频页或视频链接上"用 OpenNoter 总结"
  try {
    browser.contextMenus.create({
      id: 'opennoter-summarize-page',
      title: '用 OpenNoter 总结此视频',
      contexts: ['page', 'link', 'video'],
      documentUrlPatterns: [
        '*://*.bilibili.com/*',
        '*://*.youtube.com/*',
        '*://youtu.be/*',
        '*://*.douyin.com/*',
        '*://*.kuaishou.com/*',
      ],
    })
  }
  catch (e) {
    console.warn('注册右键菜单失败：', e)
  }
})

browser.contextMenus?.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== 'opennoter-summarize-page')
    return
  const url = info.linkUrl || tab?.url
  if (!url)
    return
  const result = await startTask(url, tab?.title)
  if (result.ok)
    await openSidePanelInTab(tab?.id)
  else
    console.warn('右键启动失败：', result.error)
})

// content script 占位握手 —— 未来可扩展为查询当前任务等
onMessage('get-current-tab', async () => {
  try {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true })
    return { title: tab?.title, url: tab?.url }
  }
  catch {
    return { title: undefined, url: undefined }
  }
})
