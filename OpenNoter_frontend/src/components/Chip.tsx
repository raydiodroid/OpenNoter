import type { ReactNode } from 'react'
import { ActionButton } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import DeleteIcon from '@react-spectrum/s2/icons/Delete'

/**
 * 胶囊标签：可显示一行短文本，并可选带一个移除按钮。
 *
 * 规格：胶囊圆角 full、内边距 4/8、layer-2 底 + gray-200 边框、
 * 正文 ui-sm；移除按钮是 quiet ActionButton，保留可见焦点环。
 */

const chip = style({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  paddingStart: 8,
  paddingEnd: 4,
  paddingY: 4,
  borderRadius: 'full',
  backgroundColor: 'layer-2',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  font: 'ui-sm',
  color: 'neutral',
})

export function Chip({
  children,
  onRemove,
  removeLabel,
}: {
  children: ReactNode
  /** 传入则显示移除按钮 */
  onRemove?: () => void
  /** 移除按钮的无障碍名称，必须能说明移除的是哪一个 */
  removeLabel?: string
}) {
  return (
    <span className={chip}>
      {children}
      {onRemove ? (
        <ActionButton isQuiet size="S" aria-label={removeLabel} onPress={onRemove}>
          <DeleteIcon />
        </ActionButton>
      ) : null}
    </span>
  )
}
