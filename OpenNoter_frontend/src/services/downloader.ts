import request, { API_BASE } from '@/lib/api'

export type DownloaderPlatform = 'bilibili' | 'youtube' | 'douyin' | 'kuaishou'

export const getDownloaderCookie = async (
  id: string,
  options?: { suppressToast?: boolean },
) => {
  return await request.get<never, { cookie?: string | null }>(
    '/get_downloader_cookie/' + id,
    options,
  )
}

export const updateDownloaderCookie = async (data: { cookie: string; platform: DownloaderPlatform }) => {
  return await request.post('/update_downloader_cookie', data)
}

export async function requestBrowserCookieTransfer(platform: DownloaderPlatform): Promise<number> {
  const expectedOrigin = window.location.origin
  if (new URL(API_BASE, expectedOrigin).origin !== expectedOrigin)
    throw new Error('浏览器扩展 Cookie 获取要求前端与 API 同源，请将 VITE_API_BASE_URL 配置为 /api')

  const { transfer_id } = await request.post<never, { transfer_id: string }>(
    '/create_browser_cookie_transfer',
    { platform },
  )
  const requestId = crypto.randomUUID()

  return new Promise((resolve, reject) => {
    const cleanup = () => {
      window.removeEventListener('message', onMessage)
      window.clearTimeout(timeout)
    }
    const timeout = window.setTimeout(() => {
      cleanup()
      reject(new Error('未检测到 OpenNoter 浏览器扩展，请安装并启用扩展后重试'))
    }, 15000)
    const onMessage = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== expectedOrigin)
        return
      const result = event.data as Record<string, unknown> | null
      if (!result || result.source !== 'opennoter-extension' || result.type !== 'cookie-transfer-result' || result.requestId !== requestId)
        return
      cleanup()
      if (result.ok === true && typeof result.count === 'number')
        resolve(result.count)
      else
        reject(new Error(typeof result.error === 'string' ? result.error : '浏览器 Cookie 获取失败'))
    }

    window.addEventListener('message', onMessage)
    window.postMessage({
      source: 'opennoter-web',
      type: 'cookie-transfer-request',
      requestId,
      transferId: transfer_id,
      platform,
    }, expectedOrigin)
  })
}

export interface BilibiliQrLoginStart {
  session_id: string
  qr_url: string
  expires_in: number
}

export type BilibiliQrLoginStatus = 'waiting' | 'scanned' | 'success' | 'expired' | 'failed'

export async function startBilibiliQrLogin(): Promise<BilibiliQrLoginStart> {
  const response = await request.post<never, BilibiliQrLoginStart>('/bilibili_qr_login')
  return response
}

export async function pollBilibiliQrLogin(sessionId: string): Promise<BilibiliQrLoginStatus> {
  const response = await request.get<never, { status: BilibiliQrLoginStatus }>(
    `/bilibili_qr_login/${encodeURIComponent(sessionId)}`,
    { suppressToast: true },
  )
  return response.status
}

export async function cancelBilibiliQrLogin(sessionId: string): Promise<void> {
  await request.delete(`/bilibili_qr_login/${encodeURIComponent(sessionId)}`, { suppressToast: true })
}
