import type { ReactNode } from 'react'
import { Content, Heading, IllustratedMessage, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

/**
 * 全站唯一的页面骨架。
 *
 * 对齐 Spectrum 2：
 * - 页面标题属于内容区，不是卡片；h1 用 Heading L（28px），不加自定行高
 * - 页面内容最大宽度 1200px，四周 32px；结构按 app frame 内容区留白
 * - 分组标题用 Title（16px / 500），Sentence case，不做大写/宽字距
 * - 容器用背景色或边框二选一，圆角用 large（10px），不凭空造 20px
 */

const shell = style({
  height: 'full',
  overflowY: 'auto',
})

const inner = style({
  width: 'full',
  boxSizing: 'border-box',
  maxWidth: 1200,
  marginX: 'auto',
  paddingX: 32,
  paddingY: 32,
  display: 'flex',
  flexDirection: 'column',
  gap: 32,
})

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className={shell}>
      <div className={inner}>{children}</div>
    </div>
  )
}

const header = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
})

const titleRow = style({
  display: 'flex',
  alignItems: 'start',
  justifyContent: 'space-between',
  gap: 24,
})

const titleGroup = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  minWidth: 0,
})

const title = style({
  font: 'heading-lg',
  color: 'neutral',
  margin: 0,
  textWrap: 'balance',
})

const description = style({
  font: 'body',
  color: 'neutral-subdued',
  margin: 0,
  maxWidth: 720,
})

const headerActions = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  flexShrink: 0,
  paddingTop: 4,
})

export function PageHeader({
  title: heading,
  description: desc,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className={header}>
      <div className={titleRow}>
        <div className={titleGroup}>
          <h1 className={title}>{heading}</h1>
          {desc ? <p className={description}>{desc}</p> : null}
        </div>
        {actions ? <div className={headerActions}>{actions}</div> : null}
      </div>
    </header>
  )
}

const section = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
})

const sectionHead = style({
  display: 'flex',
  alignItems: 'end',
  justifyContent: 'space-between',
  gap: 12,
  minHeight: 24,
})

const sectionLabel = style({
  font: 'title',
  color: 'neutral',
  margin: 0,
})

const sectionDescription = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
  maxWidth: 720,
})

/** 分组右侧的操作区 */
const sectionActions = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  flexShrink: 0,
})

/** 分组内容 */
const sectionContent = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
})

/**
 * 页面里的一个语义分组。
 * `title` 是分组标签（Title），不是卡片标题；卡片标题请放进容器内部。
 */
export function Section({
  title,
  description,
  actions,
  children,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={section}>
      {title || actions ? (
        <div className={sectionHead}>
          {title ? <h2 className={sectionLabel}>{title}</h2> : <span />}
          {actions ? <div className={sectionActions}>{actions}</div> : null}
        </div>
      ) : null}
      {description ? <p className={sectionDescription}>{description}</p> : null}
      <div className={sectionContent}>{children}</div>
    </section>
  )
}

const fillPanel = style({
  backgroundColor: 'layer-1',
  borderRadius: 'lg',
  padding: 24,
})

/**
 * 内容容器：浅灰底（layer-1）+ 10px 圆角 + 24px 内边距，用于表单、指标卡等成组内容。
 *
 * 不提供「白底 + 边框」变体：Spectrum 的 TableView 自带 1px gray-300 边框与 6px 圆角，
 * 再套一层描边容器会变成双层边框。需要白底描边时直接用 S2 组件自己的容器。
 */
export function Panel({ children, padded = true }: { children: ReactNode; padded?: boolean }) {
  return (
    <section className={fillPanel} style={padded ? undefined : { padding: 0 }}>
      {children}
    </section>
  )
}

/* ── 配置页统一排版 ───────────────────────────────────────────
   左侧固定宽度的项目列表 + 右侧自适应的详情区，是 Spectrum 2
   内容区「垂直分栏」布局的常见形态：左列用于在同类条目间切换，
   右列承载当前条目的表单。 */

const listDetail = style({
  display: 'flex',
  gap: 24,
  alignItems: 'start',
})

const listColumn = style({
  width: 280,
  flexShrink: 0,
})

const detailColumn = style({
  flexGrow: 1,
  flexShrink: 1,
  minWidth: 0,
})

export function ListDetail({ list, children }: { list: ReactNode; children: ReactNode }) {
  return (
    <div className={listDetail}>
      <div className={listColumn}>{list}</div>
      <div className={detailColumn}>{children}</div>
    </div>
  )
}

/** 容器内的说明文字：Detail 字号、neutral-subdued */
export const helperText = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
})

/** 表单宽度上限，避免长表单在大屏上拉得过宽 */
export const formWidth = style({ maxWidth: 720 })

const emptyState = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  paddingY: 48,
  paddingX: 24,
})

/**
 * 统一的空状态：S2 插画 + 标题 + 说明。
 *
 * 用 `IllustratedMessage size="S"`，插画传 S2 的 illustrations（不是 20px 的
 * 图标）—— 插画槽位有固定高度，塞图标会缩成一个小点。
 */
export function EmptyState({
  illustration,
  title,
  children,
}: {
  illustration?: ReactNode
  title: string
  children?: ReactNode
}) {
  return (
    <div className={emptyState}>
      <IllustratedMessage size="S">
        {illustration}
        <Heading>{title}</Heading>
        {children ? <Content>{children}</Content> : null}
      </IllustratedMessage>
    </div>
  )
}

/** 统一的加载态 */
export function LoadingState({ label = '加载中…' }: { label?: string }) {
  return (
    <div className={emptyState}>
      <Text styles={helperText}>{label}</Text>
    </div>
  )
}
