import { useEffect, useState } from 'react'
import { ActionButton, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CloseIcon from '@react-spectrum/s2/icons/Close'
import { Notice } from '@/components/Notice'

type Severity = 'info' | 'warning' | 'error'

interface DiagnosticPayload {
  exe_path?: string
  path_has_non_ascii?: boolean
  path_has_space?: boolean
  parent_writable?: boolean
}

interface BannerState {
  severity: Severity
  title: string
  detail: string
  dismissible: boolean
}

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

const wrapper = style({
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  zIndex: 9999,
  padding: 8,
})

const row = style({
  display: 'flex',
  alignItems: 'start',
  gap: 8,
})

const body = style({ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 })
const detail = style({ font: 'detail-sm', whiteSpace: 'pre-wrap', margin: 0 })

function describeWarning(payload: DiagnosticPayload) {
  const parts: string[] = []
  if (payload.path_has_non_ascii) parts.push('安装路径包含非 ASCII 字符（中文 / 日文等）')
  if (payload.path_has_space) parts.push('安装路径包含空格')
  if (payload.parent_writable === false) parts.push('安装目录不可写')

  return {
    title: '检测到可能导致后端启动失败的安装路径',
    detail:
      `${parts.join('；')}。\n` +
      '建议把 OpenNoter 安装到纯英文、无空格、可写的路径下。\n' +
      `当前路径：${payload.exe_path || '未知'}`,
  }
}

export default function StartupBanner() {
  const [banner, setBanner] = useState<BannerState | null>(null)

  useEffect(() => {
    if (!isTauri) return
    let unlisteners: Array<() => void> = []

    ;(async () => {
      const { listen } = await import('@tauri-apps/api/event')

      const offWarning = await listen<DiagnosticPayload>('backend-warning', event => {
        const { title, detail } = describeWarning(event.payload || {})
        setBanner({ severity: 'warning', title, detail, dismissible: true })
      })

      const offTerminated = await listen<number | null>('backend-terminated', event => {
        setBanner({
          severity: 'error',
          title: '后端进程已退出',
          detail: `退出码：${event.payload ?? '未知'}。打开「部署监控」或重启应用以恢复。`,
          dismissible: false,
        })
      })

      const clearError = () => setBanner(current => (current?.severity === 'error' ? null : current))
      const offRestarted = await listen('backend-restarted', clearError)
      const offReady = await listen('backend-ready', clearError)

      unlisteners = [offWarning, offTerminated, offRestarted, offReady]
    })()

    return () => unlisteners.forEach(fn => fn())
  }, [])

  if (!banner) return null

  const variant =
    banner.severity === 'error' ? 'negative' : banner.severity === 'warning' ? 'notice' : 'informative'

  return (
    <div className={wrapper}>
      <Notice variant={variant}>
        <div className={row}>
          <div className={body}>
            <Text>{banner.title}</Text>
            <Text styles={detail}>{banner.detail}</Text>
          </div>
          {banner.dismissible ? (
            <ActionButton isQuiet size="S" aria-label="关闭" onPress={() => setBanner(null)}>
              <CloseIcon />
            </ActionButton>
          ) : null}
        </div>
      </Notice>
    </div>
  )
}
