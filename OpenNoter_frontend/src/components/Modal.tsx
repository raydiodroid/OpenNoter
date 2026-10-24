import { useEffect, useRef, type ReactNode } from 'react'
import { CloseButton } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

/**
 * 轻量模态壳：S2 DialogContainer 在 React 19.2 下会把内容重复挂载，
 * 这里用固定的 backdrop + elevated 卡片实现同一套视觉，并保留 Escape 关闭、
 * 初始焦点、Tab 焦点循环与关闭后的焦点归还。
 * 内容仍是 S2 组件；可关闭时点遮罩关闭，不可关闭（启动/失败）时不响应遮罩。
 *
 * 右上角的 X 关闭按钮与 S2 Dialog 的行为对齐 —— 之前只有 Esc 和点遮罩
 * 能关，界面上看不到任何关闭入口。
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/** 右上角关闭按钮：初始焦点要跳过它，落在正文上 */
const CLOSE_MARK = 'data-modal-close'
const backdrop = style({
  position: 'fixed',
  inset: 0,
  zIndex: 10000,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 24,
  backgroundColor: 'transparent-overlay-500',
})

const card = style({
  position: 'relative',
  width: 'full',
  boxSizing: 'border-box',
  maxHeight: '[calc(100vh - 48px)]',
  overflowY: 'auto',
  padding: 32,
  borderRadius: 'xl',
  backgroundColor: 'elevated',
  boxShadow: 'emphasized',
  outlineStyle: 'none',
})

/** 有右上角关闭按钮时，顶部多留一段给按钮，正文不至于顶到按钮上 */
const cardDismissible = style({
  position: 'relative',
  width: 'full',
  boxSizing: 'border-box',
  maxHeight: '[calc(100vh - 48px)]',
  overflowY: 'auto',
  padding: 32,
  paddingTop: 56,
  borderRadius: 'xl',
  backgroundColor: 'elevated',
  boxShadow: 'emphasized',
  outlineStyle: 'none',
})

const closeButton = style({
  position: 'absolute',
  top: 12,
  right: 12,
})

export function Modal({
  open,
  label,
  width = 480,
  onClose,
  children,
}: {
  open: boolean
  label: string
  width?: number
  onClose?: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)

  // 打开时把焦点移进弹层，关闭时归还给触发元素
  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const node = ref.current
    // 初始焦点落在正文上，而不是右上角的关闭按钮
    const first = Array.from(node?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).find(
      element => !element.hasAttribute(CLOSE_MARK)
    )
    ;(first ?? node)?.focus()
    return () => {
      restoreRef.current?.focus?.()
    }
  }, [open])

  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (onClose) onClose()
        return
      }
      if (event.key !== 'Tab') return

      // 焦点循环：不让 Tab 跑到弹层外面
      const node = ref.current
      if (!node) return
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        element => element.offsetWidth > 0 || element.offsetHeight > 0
      )
      if (items.length === 0) {
        event.preventDefault()
        node.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === node)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className={backdrop}
      onClick={event => {
        if (onClose && event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={ref}
        className={onClose ? cardDismissible : card}
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
      >
        {onClose ? (
          <div className={closeButton}>
            <CloseButton data-modal-close aria-label="关闭" size="S" onPress={onClose} />
          </div>
        ) : null}
        {children}
      </div>
    </div>
  )
}
