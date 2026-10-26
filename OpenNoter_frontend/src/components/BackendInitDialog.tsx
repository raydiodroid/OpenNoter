import { useEffect, useMemo, useState } from 'react'
import { Button, Content, Heading, Link, ProgressCircle, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { useBackendEvents } from '@/components/BackendHealth/useBackendEvents'
import { Notice } from '@/components/Notice'
import { Modal } from '@/components/Modal'

const STDERR_PREVIEW_LINES = 6

const stack = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const logBox = style({
  maxHeight: 160,
  overflow: 'auto',
  padding: 8,
  borderRadius: 'default',
  backgroundColor: 'layer-2',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  font: 'code-sm',
  whiteSpace: 'pre-wrap',
  margin: 0,
})
const actions = style({ display: 'flex', flexWrap: 'wrap', gap: 8 })
const statusRow = style({ display: 'flex', alignItems: 'center', gap: 12 })

export default function BackendInitDialog({
  open,
  failed = false,
  lastError = null,
  onRetry,
}: {
  open: boolean
  failed?: boolean
  lastError?: string | null
  onRetry?: () => void
}) {
  const { isTauri, restart, copyLogs, logs } = useBackendEvents()
  const [restarting, setRestarting] = useState(false)
  const [copyState, setCopyState] = useState<'idle' | 'ok' | 'fail'>('idle')

  const stderrPreview = useMemo(() => {
    if (!failed || !logs?.length) return []
    return logs
      .filter(entry => entry.level === 'error')
      .slice(-STDERR_PREVIEW_LINES)
      .map(entry => entry.text)
  }, [failed, logs])

  // ---------------------------------------------------------------
  // 延迟显示（只在 Web 端生效）
  // ---------------------------------------------------------------
  // ## 为什么需要
  //
  // `useCheckBackend` 的初始状态是 `loading: true`，而探测是异步的
  // （`useEffect` 里才发第一个 `/sys_check`）。所以**首帧必然**是
  // 「后端正在初始化」—— 哪怕后端早就在跑、`/sys_check` 只要 2ms。
  //
  // Web 部署（docker compose）下后端是常驻的，根本没「解压 10-30 秒」这回事。
  // 结果就是每次打开/刷新页面都会闪一个全屏 Modal 遮罩，虽然很短但很难看，
  // 而且文案（首次启动解压依赖）对 Web 用户是误导。
  //
  // ## 做法
  //
  // 给一个很短的宽限期（300ms）：
  //   - 后端正常（< 300ms 就绪）→ **从不显示** Modal，页面直接出来；
  //   - 后端真的慢/没起 → 300ms 后照常显示「正在初始化」。
  //
  // 桌面端**不加延迟**：sidecar 解压确实要 5-15 秒，早点给用户反馈更好，
  // 而且那条文案在桌面端是准确的。
  const [graceElapsed, setGraceElapsed] = useState(false)
  const WEB_GRACE_MS = 300

  useEffect(() => {
    // 桌面端 / 已经过了宽限期 / 已经失败（要立刻显示错误）→ 不需要再等
    if (isTauri || graceElapsed || failed) return
    const t = setTimeout(() => setGraceElapsed(true), WEB_GRACE_MS)
    return () => clearTimeout(t)
  }, [isTauri, graceElapsed, failed])

  // Web 端在宽限期内不显示；桌面端与失败态立即显示
  const shouldShowLoading = isTauri || graceElapsed

  const isOpen = (open && shouldShowLoading) || failed

  const handleRestart = async () => {
    setRestarting(true)
    try {
      if (isTauri) await restart()
      onRetry?.()
    } finally {
      setRestarting(false)
    }
  }

  const handleCopy = async () => {
    const ok = await copyLogs()
    setCopyState(ok ? 'ok' : 'fail')
    setTimeout(() => setCopyState('idle'), 2000)
  }

  return (
    <Modal open={isOpen} label={failed ? '后端启动失败' : '后端正在初始化'}>
      {failed ? (
        <div className={stack}>
          <Heading level={2}>后端启动失败</Heading>
          <Text styles={muted}>{lastError || '后端在预计时间内未就绪。'}</Text>

          {stderrPreview.length > 0 ? (
            <Notice variant="negative">
              <Text>最近 {stderrPreview.length} 行错误日志</Text>
            </Notice>
          ) : null}
          {stderrPreview.length > 0 ? (
            <pre className={logBox}>{stderrPreview.join('\n')}</pre>
          ) : null}

          <div className={stack}>
            <Text styles={muted}>常见原因：</Text>
            <Text styles={muted}>
              · 安装路径含中文 / 空格；· 缺少 ffmpeg 或端口 8483 被占用；· 首次启动模型下载未完成。
            </Text>
          </div>

          <div className={actions}>
            <Button variant="accent" onPress={handleRestart} isDisabled={restarting}>
              {isTauri ? (restarting ? '重启中…' : '重启后端') : '重试'}
            </Button>
            {isTauri ? (
              <Button variant="secondary" onPress={handleCopy}>
                {copyState === 'ok' ? '已复制' : copyState === 'fail' ? '复制失败' : '复制启动日志'}
              </Button>
            ) : null}
          </div>

          <Text styles={muted}>
            仍然无法解决？复制日志去{' '}
            <Link href="https://github.com/JefferyHcool/BiliNote/issues" target="_blank">
              GitHub Issues
            </Link>{' '}
            反馈。
          </Text>
        </div>
      ) : (
        <div className={stack}>
          <div className={statusRow}>
            <ProgressCircle aria-label="启动中" isIndeterminate size="S" />
            <Heading level={2}>后端正在初始化</Heading>
          </div>
          <Content>首次启动可能需要 10–30 秒解压依赖，请稍候。</Content>
        </div>
      )}
    </Modal>
  )
}
