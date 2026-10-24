import type { ComponentType } from 'react'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import AILogo from '@/components/AILogo'

/** 品牌标容器：固定方框，不参与压缩 */
const brandBox = style({
  display: 'inline-flex',
  flexShrink: 0,
})

/** 平台品牌标：图是固定 200×200 的 SVG，外层统一裁到一个方框里 */
export function PlatformMark({ Logo, size = 16 }: { Logo: ComponentType; size?: number }) {
  return (
    <span className={`brand-mark ${brandBox}`} style={{ width: size, height: size }}>
      <Logo />
    </span>
  )
}

/**
 * 供应商 logo。S2 没有品牌图标，这里沿用 @lobehub/icons 的静态白名单，
 * 统一放进圆角方框，视觉重量与 S2 的 20px 线性图标对齐。
 */
export function ProviderMark({
  name,
  size = 20,
  boxed = true,
}: {
  name: string
  size?: number
  boxed?: boolean
}) {
  return <AILogo name={name} size={size} boxed={boxed} />
}
