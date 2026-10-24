import { useLayoutEffect, useRef, type ReactNode } from 'react'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

/**
 * 区域展开/收起容器。
 *
 * ## 为什么用 grid 行高而不是测高
 * S2 的 TableView 是虚拟化表格，行由 JS 写入绝对定位并带 `contain: size`，对行/表体做
 * `height` 过渡必然被下一帧覆盖；而 `grid-template-rows: 0fr → 1fr` 不需要 JS 测高，
 * 内容异步变高（图片、数据刷新）也能自然跟随，不用在 transitionend 后二次修正。
 * 三端原生支持：Chrome 107 / Safari 16 / Firefox 66。
 *
 * ## 为什么内层必须有 min-height:0 + overflow:clip
 * 少了 min-height:0，0fr 会被内容撑开、动画完全失效；用 clip 而不是 hidden，
 * 是因为 hidden 会创建滚动容器，动画期间若内部有 :focus 元素，浏览器会为显示焦点
 * 而改变 scrollTop，导致内容在动画中「跳一下」。
 *
 * ## 为什么折叠态用 inert 而不是 hidden
 * hidden 会置 content-visibility: hidden，元素不参与布局，grid 过渡没有可插值的高度
 * → 展开失效（实测逐帧停在 0）。inert 只影响交互与可访问树，不影响布局与过渡
 * （实测展开曲线与不加 inert 时逐帧一致），且不需要等 transitionend。
 *
 * ## 关于与父容器 gap 的关系（重要）
 * 收起态高度为 0 的 Fold 仍是父容器的「子项」，父级 `gap` 照样会在它前后留间距
 * → 屏幕上会多出空白。
 *
 * 试过用负 margin 抵消，但这条路不可行：`transition-property` 会被后加载的类整体覆盖，
 * 高度与 margin 无法共用一条干净的过渡（实测收起时高度动画被 margin 动画顶掉）。
 *
 * **正确用法**：把 Fold 放进一个自身不设 gap 的容器，由 Fold 的 `gap` 负责内部间距。
 * 更省事的等价写法：相邻的固定块之间也各包一个 Fold，这样「所有竖向间距」都由
 * Fold 统一管理，任何一个收起时都没有残留空白。
 */

const DURATION = 240

const fold = style({
  display: 'grid',
  gridTemplateRows: '[0fr]',
  transition: {
    default: '[grid-template-rows]',
    '@media (prefers-reduced-motion: reduce)': 'none',
  },
  transitionDuration: DURATION,
  transitionTimingFunction: 'default',
})

const foldOpen = style({ gridTemplateRows: '[1fr]' })

/** 内层：负责裁剪，并承担子元素之间的竖向间距 */
const foldInner = style({
  minHeight: 0,
  overflow: 'clip',
  display: 'flex',
  flexDirection: 'column',
})

/** 内层子元素间距（S2 style() 只接受 spacing token，故按档位收敛） */
const innerGap = {
  0: style({ gap: 0 }),
  8: style({ gap: 8 }),
  12: style({ gap: 12 }),
  16: style({ gap: 16 }),
  24: style({ gap: 24 }),
  32: style({ gap: 32 }),
} as const

export type FoldGap = keyof typeof innerGap

export function Fold({
  open,
  gap = 0,
  children,
}: {
  open: boolean
  /** 折叠区内部子元素的竖向间距；同时也是「与上一个兄弟项的间距」 */
  gap?: FoldGap
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)

  // 在浏览器绘制前同步切换 inert：收起瞬间就不可 Tab / 不可读屏，展开瞬间立刻恢复。
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (open) el.removeAttribute('inert')
    else el.setAttribute('inert', '')
  }, [open])

  return (
    <div
      ref={ref}
      className={open ? `${fold} ${foldOpen}` : fold}
      // 初始渲染即写 inert（裸属性 = 恒为 true），由 layout effect 在首帧按 open 修正，
      // 避免「初始 open=true 时先闪一帧不可交互」。
      inert
    >
      <div className={`${foldInner} ${innerGap[gap]}`}>{children}</div>
    </div>
  )
}
