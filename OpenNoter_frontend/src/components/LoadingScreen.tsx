import { ProgressCircle } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

/**
 * React 侧的加载态 —— 与 `index.html` 里内联的启动骨架**视觉一致**。
 *
 * ## 为什么要有两份
 *
 * | 阶段 | 渲染者 | 说明 |
 * |---|---|---|
 * | JS 尚未执行 | `index.html` 内联骨架 | 解决 bundle 下载/解析期间的白屏 |
 * | JS 已执行、后端/鉴权未就绪 | 本组件 | 承接上一步，视觉无缝 |
 *
 * 两者布局、颜色、动画节奏都对齐，所以用户在切换瞬间**看不出跳变**。
 * 修改其中一处时，请同步另一处（`index.html` 的 `<style>` 段）。
 *
 * ## 为什么不用 S2 的 Skeleton 组件
 *
 * 项目用的 `@react-spectrum/s2` 没有提供 Skeleton 基础组件（只有
 * ProgressCircle 等），骨架的条状占位只能用 style() 拼。
 * 这样反而更好：能精确复刻 index.html 那版的尺寸与动画。
 */

const page = style({
  position: 'fixed',
  inset: 0,
  zIndex: 9999,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 24,
  backgroundColor: 'base',
})

const brand = style({ display: 'flex', alignItems: 'center', gap: 16 })

const logo = style({
  width: '[52px]',
  height: '[52px]',
  borderRadius: 'default',
  backgroundColor: 'accent-900',
  color: 'white',
  display: 'grid',
  placeItems: 'center',
  fontSize: '[22px]',
  fontWeight: 'bold',
})

const title = style({ font: 'title-sm', margin: 0 })

const tagline = style({ font: 'detail-sm', color: 'neutral-subdued', margin: 0 })

const progressWrap = style({ width: 168 })

const hint = style({ font: 'detail-sm', color: 'neutral-subdued', margin: 0, minHeight: '[1.2em]' })

const skeleton = style({
  width: '[min(90vw, 940px)]',
  display: 'grid',
  gridTemplateColumns: {
    default: '1fr',
    '@media (min-width: 760px)': '250px 1fr',
  },
  gap: 16,
  opacity: 0.5,
})

const skeletonCard = style({
  backgroundColor: 'elevated',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderRadius: 'lg',
  padding: 16,
  display: { default: 'none', '@media (min-width: 760px)': 'block' },
})

const skeletonMain = style({
  backgroundColor: 'elevated',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderRadius: 'lg',
  padding: 16,
})

// 骨架条：S2 的 style() 返回值可以直接传 className，
// 但**不能**用模板字符串拼接（它是编译期转换的函数，不是字符串）。
// 这里为每种宽度定义完整样式，避免运行时拼接。
const barBase = {
  height: 10,
  borderRadius: 'full',
  backgroundColor: 'gray-200',
  marginBottom: 12,
  // 呼吸：暗示正在加载而不是卡死
  animation: 'pulse 1.9s ease-in-out infinite',
} as const

const barTall = style({ ...barBase, height: 15 })
const barW90 = style({ ...barBase, width: '90%' })
const barW75 = style({ ...barBase, width: '75%' })
const barW60 = style({ ...barBase, width: '60%' })
const barW45 = style({ ...barBase, width: '45%' })

export default function Loading({ hintText = '正在启动…' }: { hintText?: string }) {
  return (
    <div className={page} role="status" aria-live="polite" aria-label="正在启动 OpenNoter">
      <div className={brand}>
        <div className={logo} aria-hidden="true">
          ▶
        </div>
        <div>
          <h1 className={title}>OpenNoter</h1>
          <p className={tagline}>把视频变成结构化笔记</p>
        </div>
      </div>

      <div className={progressWrap}>
        <ProgressCircle aria-label="加载中" isIndeterminate size="S" />
      </div>
      <p className={hint}>{hintText}</p>

      <div className={skeleton} aria-hidden="true">
        <div className={skeletonCard}>
          <div className={barTall} />
          <div className={barW90} />
          <div className={barW60} />
          <div className={barW75} />
          <div className={barW45} />
        </div>
        <div className={skeletonMain}>
          <div className={barTall} />
          <div className={barW90} />
          <div className={barW90} />
          <div className={barW75} />
          <div className={barW90} />
          <div className={barW45} />
        </div>
      </div>
    </div>
  )
}
