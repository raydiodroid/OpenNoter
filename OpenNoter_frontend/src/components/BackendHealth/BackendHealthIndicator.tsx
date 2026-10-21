import { useEffect, useState } from 'react'
import { ActionButton, Button, Content, Heading, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { useBackendEvents } from './useBackendEvents'
import { Modal } from '@/components/Modal'
import BackendLogPanel from './BackendLogPanel'

type Health = 'green' | 'yellow' | 'red' | 'unknown'

const HEALTH_POLL_MS = 5000
const SYS_HEALTH_PATH = '/sys_health'

function backendBase(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL as string | undefined
  return (fromEnv && fromEnv.length > 0 ? fromEnv : '/api').replace(/\/$/, '')
}

const floater = style({
  position: 'fixed',
  bottom: 12,
  insetEnd: 12,
  zIndex: 9998,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  paddingX: 12,
  paddingY: 4,
  borderRadius: 'full',
  backgroundColor: 'elevated',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  boxShadow: 'emphasized',
})

const dotBase = style({ width: 8, height: 8, borderRadius: 'full', backgroundColor: 'gray-400' })
const dotGreen = style({ backgroundColor: 'positive' })
const dotYellow = style({ backgroundColor: 'notice' })
const dotRed = style({ backgroundColor: 'negative' })

const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const actions = style({ display: 'flex', gap: 8, flexWrap: 'wrap' })
const stack = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const floaterLabel = style({ font: 'detail-sm', color: 'neutral' })

export default function BackendHealthIndicator() {
  const { status, isTauri, exitCode, logs, restart, copyLogs } = useBackendEvents()
  const [open, setOpen] = useState(false)
  const [failures, setFailures] = useState(0)
  const [lastOk, setLastOk] = useState<boolean | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'ok' | 'fail'>('idle')

  useEffect(() => {
    if (!isTauri) return
    let mounted = true

    const ping = async () => {
      try {
        const res = await fetch(`${backendBase()}${SYS_HEALTH_PATH}`)
        if (!mounted) return
        if (res.ok) {
          setFailures(0)
          setLastOk(true)
        } else {
          setFailures(count => count + 1)
          setLastOk(false)
        }
      } catch {
        if (!mounted) return
        setFailures(count => count + 1)
        setLastOk(false)
      }
    }

    ping()
    const timer = setInterval(ping, HEALTH_POLL_MS)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [isTauri])

  if (!isTauri) return null

  const health: Health =
    status === 'terminated' || failures >= 3
      ? 'red'
      : lastOk === false
        ? 'yellow'
        : lastOk === true
          ? 'green'
          : 'unknown'

  const label =
    health === 'green'
      ? '后端运行正常'
      : health === 'yellow'
        ? '后端运行中（部分检查未通过）'
        : health === 'red'
          ? status === 'terminated'
            ? `后端已退出 (code=${exitCode ?? 'unknown'})`
            : '后端无响应'
          : '后端状态未知'

  const dot =
    health === 'green'
      ? `${dotBase} ${dotGreen}`
      : health === 'yellow'
        ? `${dotBase} ${dotYellow}`
        : health === 'red'
          ? `${dotBase} ${dotRed}`
          : dotBase

  const copy = async () => {
    const ok = await copyLogs()
    setCopyState(ok ? 'ok' : 'fail')
    setTimeout(() => setCopyState('idle'), 2000)
  }

  return (
    <>
      <button
        type="button"
        className={`${floater} focus-ring`}
        title={label}
        onClick={() => setOpen(true)}
      >
        <span className={dot} />
        <Text styles={floaterLabel}>后端</Text>
      </button>

      <Modal open={open} label="后端状态" width={560} onClose={() => setOpen(false)}>
        <div className={stack}>
          <Heading level={2}>后端状态</Heading>
          <Text styles={muted}>{label}</Text>

          <BackendLogPanel status={status} exitCode={exitCode} logs={logs} />

          <div className={actions}>
            <Button
              variant="accent"
              onPress={() => {
                restart()
              }}
            >
              重启后端
            </Button>
            <Button variant="secondary" onPress={copy}>
              {copyState === 'ok' ? '已复制' : copyState === 'fail' ? '复制失败' : '复制日志'}
            </Button>
            <ActionButton onPress={() => setOpen(false)}>
              <Text>关闭</Text>
            </ActionButton>
          </div>

          <Content>
            日志最多保留最近 200 行。若后端反复退出，先检查端口 8483 与 ffmpeg 是否可用。
          </Content>
        </div>
      </Modal>
    </>
  )
}
