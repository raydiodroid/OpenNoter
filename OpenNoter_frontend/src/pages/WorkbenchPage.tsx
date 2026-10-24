import { useEffect, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { ActionButton, Tab, TabList, TabPanel, Tabs } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import ChevronLeft from '@react-spectrum/s2/icons/ChevronDoubleLeft'
import ChevronRight from '@react-spectrum/s2/icons/ChevronDoubleRight'

import NoteForm from '@/pages/workbench/NoteForm'
import NotePreview from '@/pages/workbench/NotePreview'
import HistoryPanel from '@/pages/workbench/HistoryPanel'

const page = style({
  display: 'flex',
  height: 'full',
  minHeight: 0,
  overflow: 'hidden',
})

const MIN_PANEL_WIDTH = 320
const MAX_PANEL_WIDTH = 560
const DEFAULT_PANEL_WIDTH = 380
const PANEL_WIDTH_KEY = 'opennoter.workbench.panelWidth'

const panelFrame = style({
  // 折叠轨道（rail）是绝对定位的，必须由本框充当包含块，
  // 否则它会以 App 根容器为基准，跑到屏幕最左上角压住顶栏 logo 与侧边导航。
  position: 'relative',
  display: 'flex',
  flex: '0 0 auto',
  height: 'full',
  minHeight: 0,
  // 这里不能设 overflow:hidden：拖拽手柄要跨在面板与预览区的分界线上，
  // 被裁掉就只剩一半，那 1px 分隔线也会被手柄盖住而「消失」。
  // 面板内容自身的裁剪交给 aside 的 overflow:hidden + maxWidth:'100%'。
})

const panel = style({
  position: 'relative',
  display: 'flex',
  flex: '0 0 auto',
  flexDirection: 'column',
  height: 'full',
  minWidth: 0,
  minHeight: 0,
  // 折叠时面板框只有 44px，aside 不能超出它，否则会盖住预览区
  maxWidth: '100%',
  overflow: 'hidden',
  backgroundColor: 'base',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const header = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  height: 48,
  paddingX: 16,
  boxSizing: 'border-box',
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 1,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const tabList = style({
  flexGrow: 1,
  minWidth: 0,
})

/** Tabs 铺满左栏高度 */
const tabsFull = style({ height: 'full' })

const rail = style({
  position: 'absolute',
  insetBlockStart: 0,
  insetInlineStart: 0,
  zIndex: 1,
  width: 44,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  height: 'full',
  minHeight: 0,
  overflow: 'hidden',
  // 12px + 24px 按钮 = 图标中心落在 24px 处，与展开态 header（48px 高）
  // 里的「收起」按钮同高；用 8px 会让图标在折叠时上漂 4px。
  paddingTop: 12,
  backgroundColor: 'base',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 1,
})

const body = style({
  flexGrow: 1,
  minHeight: 0,
})

const previewWrap = style({
  flexGrow: 1,
  flexShrink: 1,
  minWidth: 0,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: 'base',
})

type TabKey = 'generate' | 'history'

function maxPanelWidth() {
  if (typeof window === 'undefined') return MAX_PANEL_WIDTH
  return Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, window.innerWidth - 224 - 360))
}

function clampPanelWidth(width: number) {
  return Math.max(MIN_PANEL_WIDTH, Math.min(maxPanelWidth(), width))
}

function initialPanelWidth() {
  try {
    const saved = Number(localStorage.getItem(PANEL_WIDTH_KEY))
    return clampPanelWidth(saved || DEFAULT_PANEL_WIDTH)
  } catch {
    return DEFAULT_PANEL_WIDTH
  }
}

export default function WorkbenchPage() {
  const [collapsed, setCollapsed] = useState(false)
  const [contentVisible, setContentVisible] = useState(true)
  const [narrow, setNarrow] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < 920,
  )
  const [tab, setTab] = useState<TabKey>('generate')
  const [panelWidth, setPanelWidth] = useState(initialPanelWidth)
  const [resizing, setResizing] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_WIDTH_KEY, String(Math.round(panelWidth)))
    } catch {
      // localStorage 不可用时只影响偏好保存
    }
  }, [panelWidth])

  useEffect(() => {
    const onResize = () => {
      setNarrow(window.innerWidth < 920)
      setPanelWidth(width => clampPanelWidth(width))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    if (!resizing) return
    const previousUserSelect = document.body.style.userSelect
    const previousCursor = document.body.style.cursor
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    return () => {
      document.body.style.userSelect = previousUserSelect
      document.body.style.cursor = previousCursor
    }
  }, [resizing])

  /** 收起：内容立即隐藏，只动画面板宽度（S2 SideNavPanel 的时序） */
  const collapsePanel = () => {
    setContentVisible(false)
    setCollapsed(true)
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>('.workbench-panel-rail--visible button')?.focus()
    })
  }

  /** 展开：先动画宽度，宽度稳定后再显示内容 */
  const expandPanel = () => {
    setCollapsed(false)
    // 内容此时还是 visibility:hidden，焦点必须等它显示出来再给，
    // 否则 focus() 落在隐藏元素上等于没给，键盘用户会「丢失焦点」。
    window.setTimeout(() => {
      setContentVisible(true)
      window.requestAnimationFrame(() => {
        document
          .querySelector<HTMLButtonElement>(
            '.workbench-panel-content button[aria-label="收起参数面板"]'
          )
          ?.focus()
      })
    }, 200)
  }

  const onResizePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const startWidth = panelWidth
    setResizing(true)

    const onMove = (moveEvent: globalThis.PointerEvent) => {
      setPanelWidth(clampPanelWidth(startWidth + moveEvent.clientX - startX))
    }
    const stop = () => {
      setResizing(false)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
  }

  const onResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 16
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setPanelWidth(width => clampPanelWidth(width - step))
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      setPanelWidth(width => clampPanelWidth(width + step))
    } else if (event.key === 'Home') {
      event.preventDefault()
      setPanelWidth(MIN_PANEL_WIDTH)
    } else if (event.key === 'End') {
      event.preventDefault()
      setPanelWidth(maxPanelWidth())
    }
  }

  return (
    <div className={`${page} workbench-page${narrow ? ' workbench-page--narrow' : ''}`}>
      {narrow && !collapsed ? (
        <div
          className="workbench-panel-backdrop"
          aria-hidden="true"
          onPointerDown={collapsePanel}
        />
      ) : null}
      <div
        className={`${panelFrame} workbench-panel-frame${resizing ? ' workbench-panel-frame--resizing' : ''}${collapsed ? ' workbench-panel-frame--collapsed' : ''}`}
        style={{ width: collapsed ? 44 : panelWidth }}
      >
        <aside
          className={`workbench-panel-content${contentVisible ? '' : ' workbench-panel-content--hidden'} ${panel}`}
          style={{ width: panelWidth }}
          aria-hidden={collapsed}
          inert={collapsed}
        >
          <Tabs
            aria-label="工作台视图"
            selectedKey={tab}
            onSelectionChange={key => setTab(String(key) as TabKey)}
            styles={tabsFull}
          >
            <div className={header}>
              <TabList aria-label="工作台视图" styles={tabList}>
                <Tab id="generate">生成笔记</Tab>
                <Tab id="history">历史</Tab>
              </TabList>
              <ActionButton isQuiet size="S" aria-label="收起参数面板" onPress={collapsePanel}>
                <ChevronLeft />
              </ActionButton>
            </div>

            <TabPanel id="generate" styles={body} UNSAFE_className="scroll-pane">
              <NoteForm />
            </TabPanel>
            <TabPanel id="history" styles={body} UNSAFE_className="scroll-pane">
              <HistoryPanel onOpen={() => setTab('generate')} />
            </TabPanel>
          </Tabs>
        </aside>

        {/*
          拖拽手柄放在面板框这一层，而不是 aside 里面：
          aside 有 overflow:hidden，手柄跨到预览区的那一半会被裁掉，
          剩下的半截正好压住 1px 分隔线 —— 结果是「分隔线消失 + 手柄被盖住」。
          放在框上则由框定位（position:relative），手柄与分隔线都完整可见。
        */}
        <div
          className={`panel-resizer${resizing ? ' panel-resizer-active' : ''}`}
          role="separator"
          aria-label="调整参数面板宽度"
          title="拖动调整参数面板宽度，双击恢复默认"
          aria-orientation="vertical"
          aria-valuemin={MIN_PANEL_WIDTH}
          aria-valuemax={maxPanelWidth()}
          aria-valuenow={Math.round(panelWidth)}
          tabIndex={narrow || collapsed ? -1 : 0}
          onPointerDown={onResizePointerDown}
          onKeyDown={onResizeKeyDown}
          onDoubleClick={() => setPanelWidth(clampPanelWidth(DEFAULT_PANEL_WIDTH))}
        />

        <div
          className={`${rail} workbench-panel-rail${collapsed ? ' workbench-panel-rail--visible' : ''}`}
          aria-hidden={!collapsed}
        >
          <ActionButton
            isQuiet
            size="S"
            aria-label="展开参数面板"
            onPress={expandPanel}
            isDisabled={!collapsed}
          >
            <ChevronRight />
          </ActionButton>
        </div>
      </div>

      <div className={previewWrap}>
        <NotePreview onRequestMoreSpace={collapsePanel} />
      </div>
    </div>
  )
}
