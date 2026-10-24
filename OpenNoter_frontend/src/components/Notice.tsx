import type { ReactNode } from 'react'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

type Variant = 'informative' | 'positive' | 'notice' | 'negative' | 'neutral'

/**
 * 行内提示条。
 *
 * 采用 S2 InlineAlert 的「border + subtle fill」语义色，但压缩到表单密度：
 * 1px 语义色边框 + 同色系浅底 + 12/8 内边距，不再用左侧色条这种自定义形态。
 * 需要标题 + 正文 + 操作时仍应使用 S2 的 InlineAlert。
 */
const box = style({
  display: 'flex',
  alignItems: 'start',
  gap: 8,
  paddingX: 12,
  paddingY: 8,
  borderRadius: 'default',
  borderStyle: 'solid',
  borderWidth: 1,
  font: 'detail',
  color: 'neutral',
})

const tones = {
  informative: style({ borderColor: 'informative-700', backgroundColor: 'informative-subtle' }),
  positive: style({ borderColor: 'positive-700', backgroundColor: 'positive-subtle' }),
  notice: style({ borderColor: 'notice-700', backgroundColor: 'notice-subtle' }),
  negative: style({ borderColor: 'negative-700', backgroundColor: 'negative-subtle' }),
  neutral: style({ borderColor: 'gray-300', backgroundColor: 'gray-50' }),
}

export function Notice({ variant = 'neutral', children }: { variant?: Variant; children: ReactNode }) {
  return (
    <div
      className={`${box} ${tones[variant]}`}
      role={variant === 'negative' ? 'alert' : 'status'}
    >
      {children}
    </div>
  )
}
