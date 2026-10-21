import { Heading, ProgressCircle, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { PROGRESS_STEPS, TASK_STATUS_LABEL } from '@/constants'
import type { TaskStatus } from '@/types'

const wrap = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  width: 'full',
  gap: 16,
  textAlign: 'center',
})

const heading = style({ font: 'heading-sm', color: 'neutral' })
const hint = style({ font: 'detail', color: 'neutral-subdued' })
const titleBlock = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  alignItems: 'center',
})

const steps = style({
  display: 'flex',
  alignItems: 'start',
  gap: 8,
  marginTop: 12,
  maxWidth: 560,
  width: 'full',
})

const stepItem = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 4,
  flexGrow: 1,
  flexBasis: 0,
})

const dotBase = style({
  width: 10,
  height: 10,
  borderRadius: 'full',
  backgroundColor: 'gray-300',
})

const dotDone = style({ backgroundColor: 'accent-900' })
const dotActive = style({
  backgroundColor: 'accent-900',
})

const stepLabel = style({ font: 'detail-sm', color: 'neutral-subdued' })
const stepLabelActive = style({ font: 'detail-sm', color: 'neutral', fontWeight: 'bold' })

export default function GenerateProgress({ status }: { status: TaskStatus }) {
  const currentIndex = PROGRESS_STEPS.findIndex(step => step.key === status)
  // PENDING / FORMATTING 没有独立步骤，归到最近的一站
  const activeIndex = currentIndex >= 0 ? currentIndex : status === 'PENDING' ? 0 : 4

  return (
    <div className={wrap}>
      <ProgressCircle aria-label="生成中" isIndeterminate size="L" />
      <div className={titleBlock}>
        <Heading level={2} styles={heading}>
          正在生成笔记
        </Heading>
        <Text styles={hint}>
          {TASK_STATUS_LABEL[status] || '处理中'} · 时长取决于视频长度，请保持页面打开
        </Text>
      </div>

      <div className={steps}>
        {PROGRESS_STEPS.map((step, index) => {
          const done = index < activeIndex
          const active = index === activeIndex
          return (
            <div key={step.key} className={stepItem}>
              <div
                className={
                  done ? `${dotBase} ${dotDone}` : active ? `${dotBase} ${dotActive}` : dotBase
                }
              />
              <span className={active ? stepLabelActive : stepLabel}>{step.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
