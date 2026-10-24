import { useEffect, useRef } from 'react'
import { Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import type { BackendStatus, LogEntry } from './useBackendEvents'

const logBox = style({
  height: 320,
  overflowY: 'auto',
  padding: 8,
  borderRadius: 'default',
  backgroundColor: 'layer-2',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  font: 'code-sm',
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
})

const line = style({ whiteSpace: 'pre-wrap', wordBreak: 'break-all' })
const lineError = style({ color: 'negative' })
const muted = style({ font: 'detail-sm', color: 'neutral-subdued' })
const stack = style({ display: 'flex', flexDirection: 'column', gap: 8 })
const metaRow = style({ display: 'flex', gap: 12, flexWrap: 'wrap' })

export default function BackendLogPanel({
  status,
  exitCode,
  logs,
}: {
  status: BackendStatus
  exitCode: number | null
  logs: LogEntry[]
}) {
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const box = boxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [logs])

  return (
    <div className={stack}>
      <div className={metaRow}>
        <Text styles={muted}>
          进程状态：{status === 'running' ? '运行中' : `已退出（code=${exitCode ?? 'unknown'}）`}
        </Text>
        <Text styles={muted}>日志 {logs.length} 行</Text>
      </div>
      <div ref={boxRef} className={`${logBox} u-mono`}>
        {logs.length === 0 ? (
          <Text styles={muted}>暂无日志。桌面端启动后 stdout / stderr 会出现在这里。</Text>
        ) : (
          logs.map((entry, index) => (
            <span
              key={`${entry.ts}-${index}`}
              className={entry.level === 'error' ? `${line} ${lineError}` : line}
            >
              {new Date(entry.ts).toISOString().slice(11, 19)} {entry.text}
            </span>
          ))
        )}
      </div>
    </div>
  )
}
