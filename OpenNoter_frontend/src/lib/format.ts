/** 用户粘贴的链接常缺协议头（如 bilibili.com/...），无 scheme 时自动补 https:// */
export function withScheme(url: string): string {
  const value = url.trim()
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`
}

/** 取 URL 的主机名，用于设置页展示「到底配的是哪个地址」 */
export function hostOf(url?: string | null): string {
  if (!url) return ''
  try {
    return new URL(url).host
  } catch {
    return url.replace(/^https?:\/\//, '').split('/')[0]
  }
}

/** 秒 → m:ss */
export function formatClock(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0
  const mins = Math.floor(safe / 60)
  const secs = Math.floor(safe % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

/** ISO 时间 → 2026-10-19 18:00 */
export function formatDateTime(value?: string | Date | null): string {
  if (!value) return ''
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return ''
  return date
    .toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
    .replace(/\//g, '-')
}

/** 后端记录的简短时间（列表用） */
export function formatShortTime(value?: string | null): string {
  if (!value) return '从未'
  return value.slice(0, 16).replace('T', ' ')
}
