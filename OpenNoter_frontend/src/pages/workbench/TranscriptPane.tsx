import { useState } from 'react'
import { Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { formatClock } from '@/lib/format'
import type { TranscriptSegment } from '@/types'

const pane = style({
  display: 'flex',
  flexDirection: 'column',
  height: 'full',
  minHeight: 0,
  backgroundColor: 'base',
  overflow: 'hidden',
})

const header = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  height: 48,
  paddingX: 16,
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 1,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const headerTitle = style({ font: 'title-sm', color: 'neutral', margin: 0 })

const list = style({
  flexGrow: 1,
  minHeight: 0,
  overflowY: 'auto',
  paddingX: 8,
  paddingY: 8,
})

/** 时间轴行：用原生 button，键盘可达并自带可见焦点环 */
const row = style({
  display: 'grid',
  gridTemplateColumns: [64, '1fr'],
  gap: 8,
  width: 'full',
  paddingX: 8,
  paddingY: 4,
  borderRadius: 'default',
  borderStyle: 'none',
  backgroundColor: 'transparent',
  color: 'inherit',
  textAlign: 'start',
  cursor: 'pointer',
})

const rowActive = style({ backgroundColor: 'accent-100' })

const time = style({
  font: 'code-sm',
  color: 'neutral-subdued',
  paddingTop: 2,
})

const segmentText = style({ font: 'ui', color: 'neutral' })

const speaker = style({ font: 'detail-sm', color: 'neutral-subdued' })

const hint = style({ font: 'body-sm', color: 'neutral-subdued' })

const count = style({ font: 'detail-sm', color: 'neutral-subdued' })

const footer = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  height: 48,
  paddingX: 16,
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 1,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const empty = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: 'full',
  padding: 24,
})

export default function TranscriptPane({ segments }: { segments: TranscriptSegment[] }) {
  const [active, setActive] = useState<number | null>(null)

  if (!segments.length) {
    return (
      <div className={pane}>
        <div className={header}>
          <h2 className={headerTitle}>原文参照</h2>
        </div>
        <div className={empty}>
          <Text styles={hint}>这条任务没有转写内容</Text>
        </div>
      </div>
    )
  }

  const total = segments[segments.length - 1]?.end ?? 0

  return (
    <div className={pane}>
      <div className={header}>
        <h2 className={headerTitle}>原文参照</h2>
        <Text styles={count}>{segments.length} 段</Text>
      </div>

      <div className={list}>
        {segments.map((segment, index) => (
          <button
            key={`${segment.start}-${index}`}
            type="button"
            className={active === index ? `${row} ${rowActive} focus-ring` : `${row} focus-ring`}
            aria-current={active === index ? 'true' : undefined}
            onClick={() => setActive(index)}
          >
            <span className={`${time} u-mono`}>{formatClock(segment.start)}</span>
            <span className={segmentText}>
              {segment.speaker ? (
                <Text styles={speaker}>
                  {segment.speaker}
                  {'  '}
                </Text>
              ) : null}
              {segment.text}
            </span>
          </button>
        ))}
      </div>

      <div className={footer}>
        <Text styles={count}>共 {segments.length} 条片段</Text>
        <Text styles={count}>总时长 {formatClock(total)}</Text>
      </div>
    </div>
  )
}
