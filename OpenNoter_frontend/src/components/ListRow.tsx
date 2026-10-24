import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

/**
 * 列表行：全站「可切换条目」的唯一写法。
 *
 * 供应商列表（ModelPage）、下载平台列表（DownloaderPage）、历史记录（HistoryPanel）
 * 三处曾各写一套行容器、选中态、悬停态、焦点环与标题排版，这里收成一份。
 *
 * 几何：行高 48 / 内边距 8 / 圆角 8（控件标度 default）
 * 状态：选中 accent-100；悬停走全局 .row-hover；键盘焦点走全局 .focus-ring
 * 交互：行主体是原生 button / a，不用 div[role=button]，因此键盘与读屏行为天然正确。
 */

/** 行容器 */
const row = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  minHeight: 48,
  padding: 8,
  borderRadius: 'default',
  transition: 'default',
})

/** 选中态 */
const rowSelected = style({ backgroundColor: 'accent-100' })

/** 行主体（按钮形态）：去掉浏览器默认外观，保留可见焦点环 */
const rowButton = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  minWidth: 0,
  flexGrow: 1,
  padding: 0,
  borderStyle: 'none',
  backgroundColor: 'transparent',
  color: 'inherit',
  textAlign: 'start',
  cursor: 'pointer',
  borderRadius: 'default',
})

/** 行主体（链接形态）：整行可点，链接语义由 <a> 提供 */
const rowLink = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  minHeight: 48,
  padding: 8,
  borderRadius: 'default',
  transition: 'default',
  textDecoration: 'none',
  color: 'neutral-subdued',
})

const rowLinkSelected = style({ color: 'neutral' })

/** 行内的两行文字块 */
const rowBody = style({
  display: 'flex',
  flexDirection: 'column',
  minWidth: 0,
  flexGrow: 1,
})

/** 行标题 */
const rowTitle = style({
  font: 'ui',
  color: 'neutral',
  fontWeight: 'bold',
  truncate: true,
})

/** 行副标题（次要信息，单行截断） */
const rowMeta = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  truncate: true,
})

/** 行副标题为一行横向信息（状态灯 + 时间） */
const rowMetaRow = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  font: 'detail-sm',
  color: 'neutral-subdued',
})

/**
 * 行容器。行主体与尾部控件由调用方组合，
 * 避免出现「可点击元素里再嵌按钮」的非法嵌套。
 */
export function ListRow({
  selected,
  className,
  children,
}: {
  selected?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <div
      className={`${row} ${selected ? rowSelected : 'row-hover'}${className ? ` ${className}` : ''}`}
    >
      {children}
    </div>
  )
}

/** 行主体：原生按钮（行内切换到某个条目） */
export function ListRowButton({
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  return (
    <button type="button" className={`${rowButton} focus-ring`} {...rest}>
      {children}
    </button>
  )
}

/** 行主体：原生链接（整行可点，跳转到另一个位置） */
export function ListRowLink({
  selected,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { selected?: boolean; children: ReactNode }) {
  return (
    <a
      className={`${rowLink} ${selected ? `${rowSelected} ${rowLinkSelected}` : 'row-hover'} focus-ring`}
      {...rest}
    >
      {children}
    </a>
  )
}

/** 行内的文字块（标题 + 副标题） */
export function ListRowBody({ children }: { children: ReactNode }) {
  return <span className={rowBody}>{children}</span>
}

/** 行标题 */
export function ListRowTitle({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className={rowTitle} title={title}>
      {children}
    </span>
  )
}

/** 行副标题 */
export function ListRowMeta({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className={rowMeta} title={title}>
      {children}
    </span>
  )
}

/** 行副标题（横向一行：状态灯 + 时间） */
export function ListRowMetaRow({ children }: { children: ReactNode }) {
  return <span className={rowMetaRow}>{children}</span>
}
