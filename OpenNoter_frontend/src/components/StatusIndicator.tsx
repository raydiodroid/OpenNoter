import type { ReactNode } from 'react'
import { Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { iconStyle } from '@react-spectrum/s2/style' with { type: 'macro' }
import CheckmarkCircleIcon from '@react-spectrum/s2/icons/CheckmarkCircle'
import CloseCircleIcon from '@react-spectrum/s2/icons/CloseCircle'
import RemoveCircleIcon from '@react-spectrum/s2/icons/RemoveCircle'

/**
 * 状态指示器：圆圈里的勾 / 叉 / 横线 + 文字。
 *
 * S2 的 `StatusLight` 只是一个实心圆点，在表格里辨识度很低；
 * 这里改用 S2 自带的圆形状态图标（CheckmarkCircle / CloseCircle / RemoveCircle），
 * 颜色通过 `iconStyle({color})` 宏走语义 token（内部写 `--iconPrimary`），
 * 明暗主题自动跟随，不需要自己写死色值。
 *
 * 形状即语义：
 * - ✓（CheckmarkCircle）完成 / 成功 / 可用
 * - ✗（CloseCircle）失败 / 不可用 / 已吊销
 * - –（RemoveCircle）进行中 / 未开始 / 未配置 / 不适用
 */

export type StatusTone = 'positive' | 'negative' | 'notice' | 'informative' | 'neutral'

const wrapper = style({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'text-to-visual',
  whiteSpace: 'nowrap',
})

const label = style({ font: 'ui', color: 'neutral' })

/**
 * 图标与配色的对应表。
 * `iconStyle` 是编译期宏，颜色必须写字面量，所以这里用查表而不是动态传参。
 */
const ICONS: Record<
  StatusTone,
  { Icon: typeof CheckmarkCircleIcon; styles: ReturnType<typeof iconStyle> }
> = {
  positive: {
    Icon: CheckmarkCircleIcon,
    styles: iconStyle({ size: 'S', color: 'positive' }),
  },
  negative: {
    Icon: CloseCircleIcon,
    styles: iconStyle({ size: 'S', color: 'negative' }),
  },
  notice: {
    Icon: RemoveCircleIcon,
    styles: iconStyle({ size: 'S', color: 'notice' }),
  },
  informative: {
    Icon: RemoveCircleIcon,
    styles: iconStyle({ size: 'S', color: 'informative' }),
  },
  neutral: {
    Icon: RemoveCircleIcon,
    styles: iconStyle({ size: 'S', color: 'neutral' }),
  },
}

export function StatusIndicator({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  const { Icon, styles } = ICONS[tone]
  return (
    <span className={wrapper}>
      <Icon styles={styles} />
      <Text styles={label}>{children}</Text>
    </span>
  )
}
