import { useEffect, useMemo, useRef, useState, type ComponentType } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  ActionButton,
  Button,
  Header,
  Menu,
  MenuItem,
  MenuSection,
  MenuTrigger,
  SearchField,
  SideNav,
  SideNavItem,
  SideNavItemContent,
  SideNavItemLink,
  SideNavPanel,
  SideNavSection,
  Text,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CloudStateOnlineIcon from '@react-spectrum/s2/icons/CloudStateOnline'
import ContrastIcon from '@react-spectrum/s2/icons/Contrast'
import DataSettingsIcon from '@react-spectrum/s2/icons/DataSettings'
import DownloadIcon from '@react-spectrum/s2/icons/Download'
import HomeIcon from '@react-spectrum/s2/icons/Home'
import InfoCircleIcon from '@react-spectrum/s2/icons/InfoCircle'
import Leave from '@react-spectrum/s2/icons/Leave'
import LockIcon from '@react-spectrum/s2/icons/Lock'
import UserGroupIcon from '@react-spectrum/s2/icons/UserGroup'

import logo from '@/assets/icon.svg'
import { useAuthStore } from '@/store/authStore'
import { useTaskStore } from '@/store/taskStore'
import { useThemeStore } from '@/store/themeStore'
import BackendHealthIndicator from '@/components/BackendHealth/BackendHealthIndicator'
import { toast } from '@/lib/toast'

interface NavItem {
  key: string
  label: string
  Icon: ComponentType
  /** 仅管理员可见。后端对应接口由 require_admin 保护，普通用户点进去只会吃 403 */
  adminOnly?: boolean
}

const SETTINGS_NAV: NavItem[] = [
  { key: '/settings/ai', label: '模型配置', Icon: DataSettingsIcon },
  { key: '/settings/download', label: '下载配置', Icon: DownloadIcon },
  { key: '/settings/keys', label: '密钥管理', Icon: LockIcon },
  { key: '/settings/users', label: '用户管理', Icon: UserGroupIcon, adminOnly: true },
  { key: '/settings/monitor', label: '部署监控', Icon: CloudStateOnlineIcon, adminOnly: true },
  { key: '/settings/about', label: '关于', Icon: InfoCircleIcon },
]

/**
 * App frame 外壳：浏览场景下 app frame 背景用 Layer 1（灰）承托 Base（白）内容区。
 * 规范里浏览场景写的是 Layer 2，但 S2 浅色主题的 Layer 2 与 Base 同为 gray-25（纯白），
 * 无法与白色内容区形成层级对比；这里取 Layer 1（gray-50），与 Layer 2 的意图一致：
 * app frame 作为底座、内容区用 Base 承载主内容。
 */
const shell = style({
  height: 'screen',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  backgroundColor: 'layer-1',
})

/**
 * App frame header：56px，产品标识 + 全局搜索 + 全局操作，与侧栏同底色。
 *
 * 右侧对齐规则（与内容表面共用同一条右边线）：
 *   header.paddingEnd === content.marginEnd === 16
 * 因此顶栏最后一个全局操作的右缘，正好落在内容表面的右缘上 —— 两者都距窗口
 * 右缘 16px，不会出现「操作比卡片更靠内 / 更靠外」的错位。
 *
 * 依据：Adobe 官方的 S2 app frame 实现在 react-spectrum.adobe.com 首页 demo 里
 * 量得 frame 右边 1232、content 右边 1216（`margin: 0 16px 0 0`）、
 * header `padding: 16px 16px 16px 20px`，最后一个 icon 按钮右缘 1216 —— 三者重合。
 */
const topbar = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 16,
  height: 56,
  paddingStart: 16,
  paddingEnd: 16,
  flexShrink: 0,
})

/** 顶部搜索：位于顶栏中部，宽度受限，避免与两侧操作抢空间 */
const searchWrap = style({
  position: 'relative',
  flexGrow: 1,
  flexShrink: 1,
  maxWidth: 420,
  marginX: 'auto',
})

const searchResults = style({
  position: 'absolute',
  insetInline: 0,
  top: 'calc(100% + 4px)',
  zIndex: 10,
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  maxHeight: 320,
  overflowY: 'auto',
  padding: 4,
  backgroundColor: 'base',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderWidth: 1,
  borderRadius: 'default',
  boxShadow: 'emphasized',
})

const searchResult = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  minHeight: 32,
  paddingX: 8,
  borderRadius: 'default',
  textAlign: 'start',
  borderStyle: 'none',
  backgroundColor: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
})

const searchResultTitle = style({
  font: 'ui',
  color: 'neutral',
  truncate: true,
})

const searchEmpty = style({
  font: 'detail',
  color: 'neutral-subdued',
  padding: 8,
})

/**
 * 品牌区：logo + 产品名。
 *
 * 左缘对齐（对齐侧栏导航图标，而不是对齐顶栏 paddingStart）：
 *   顶栏 paddingStart 16 + brand paddingStart 4 = 20
 *   === 侧栏 navPanel marginStart 8 + SideNavItem grid 首列 12 = 20
 * 这样顶栏 logo 与侧栏第一个导航图标的左缘落在同一条竖线上 —— S2 app frame
 * 的品牌与导航同属「导航区」，视觉上必须同轴，否则会显得 logo 被推右了几像素。
 * （原先 brand 用 paddingX: 8 时 logo 左缘是 24，比导航图标多 4px。）
 *
 * 尺寸：logo 20×20 与 S2 的 `ui-icon-medium`（20px，见 icons/Home.mjs）一致，
 * 即顶栏品牌与侧栏图标同级。原先 24×24 会让 logo 在一排 20px 图标上方显得偏重。
 * 垂直方向由父级 `alignItems: 'center'` 居中，顶栏高 56 时中心线为 28。
 */
const brand = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  // paddingStart 4 使 logo 左缘落在 x=20（见上方几何推导）；
  // paddingEnd 仍留 8，保证右侧点击区与文字有正常间距。
  paddingStart: 4,
  paddingEnd: 8,
  textDecoration: 'none',
  color: 'neutral',
  borderRadius: 'default',
})

const brandName = style({
  font: 'title-sm',
  color: 'neutral',
  margin: 0,
})

const logoMark = style({ borderRadius: 'default' })

/** 侧栏导航占满面板宽度 */
const navFull = style({ width: 'full' })

const topbarActions = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
})

const body = style({
  display: 'flex',
  flexGrow: 1,
  minHeight: 0,
})

/**
 * 侧栏几何：贴住窗口左缘，右侧不留白，内容表面直接接在面板右缘之后。
 *
 * 对齐依据（Adobe 官方 app frame 实测，react-spectrum.adobe.com 首页那个真实 demo）：
 *   - 侧栏面板自身的 `padding: 0 16px 16px`，左边贴着 frame 左缘（外侧无 margin）
 *   - 展开态面板右缘 === 内容表面左缘（gutter 0）
 *   - 导航项撑满面板内容宽（去掉 16px 左内边距后即为点击区整宽）
 *
 * 早先抄的 `marginX: 8` 来自官方文档里**独立组件**的 SideNavPanel 示例，不是
 * app frame 的组装配方：它会在面板左右各加 8px，于是面板与内容表面之间多出
 * 一段既不是间距节奏、也不属于任何一个容器的空白（实测 8px + 面板内部
 * 28px 的死区 ≈ 36px），看起来就是「侧栏和内容区之间空了一大块」。
 * 这里改成 `marginStart: 8` + 右侧 0：左边保留 8px 让侧栏不贴死窗口边缘，
 * 右缘由内容表面直接接管。
 *
 * 宽度 160 = S2 规范对「一级导航」的最小宽度（App frame · Side nav · Minimum width）：
 *   1 level: 160px / 2 level: 200px / 3 levels: 240px
 * 本项目是一级导航，用 160。规范同时说明 `defaultWidth` 应「自适应最长字符串」——
 * 而 160 恰好覆盖本项目最长项（4 个汉字）：
 *   12(grid 首列) + 20(icon) + 8(text-to-visual) + 56(4×14px) + 4(paddingEnd) = 100
 * 内容只需 100，160 留 60px 呼吸，是「符合规范下限 + 4 字项不贴边」的平衡点。
 * 原先抄 S2 示例的 208 会让右侧空出约 108px —— 因为 `SideNavItem` 的
 * `content` 列是 `1fr`，面板多宽、文字右边就空多少。
 *
 * - 折叠宽 42px：S2 内置的 `--collapsedWidth`，不由我们控制
 */
const navPanel = style({
  width: 160,
  marginStart: 8,
  marginEnd: 0,
  flexShrink: 0,
})

const navInner = style({
  display: 'flex',
  flexDirection: 'column',
  height: 'full',
  minHeight: 0,
  boxSizing: 'border-box',
})

/**
 * 内容区：Base 背景，与 app frame 的 Layer 1 底座形成对比。
 *
 * 右侧与顶栏共用 16px 边距（`marginEnd: 16`），使内容表面的右缘和顶栏最后一个
 * 全局操作的右缘落在同一条竖线上：两者都距窗口右缘 16px。
 * 左侧对称地由侧栏面板自身的 `marginX: 8` 与内容区留白衔接。
 *
 * 依据：Spectrum app frame 的 header 与 content area 是同一套框架的两部分，
 * 官方 demo 实测 frame 右边 − content 右边 = 16，且 content 右边与 header 最右侧
 * icon 按钮右缘完全重合。早先把表面再内缩 80px、把顶栏 paddingEnd 加到 96px 是
 * 对规范图的误读（那张图是带透视/投影的示意插画，不能直接当 px 标尺）。
 *
 * 顶部两角圆角衔接顶栏；底部不留边、不加边框，内容延伸到窗口底部。
 * 投影用 S2 的 emphasized 层级（drop-shadow-emphasized），表达内容区浮于 app frame 之上。
 */
const content = style({
  flexGrow: 1,
  flexShrink: 1,
  minWidth: 0,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  backgroundColor: 'base',
  marginEnd: 16,
  borderTopStartRadius: 'xl',
  borderTopEndRadius: 'xl',
  boxShadow: 'emphasized',
})

/**
 * 顶栏账户入口的头像本体：圆形字母头像。
 * 外层是 S2 ActionButton 的圆角方形容器，形状与默认/hover 状态由组件管理。
 */
const avatar = style({
  width: 20,
  height: 20,
  borderRadius: 'full',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: 'base',
  color: 'neutral',
  font: 'detail-sm',
  fontWeight: 'bold',
  flexShrink: 0,
})

const menuIdentity = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  paddingY: 4,
  minWidth: 180,
})

const menuName = style({
  font: 'ui',
  color: 'neutral',
  fontWeight: 'bold',
  truncate: true,
})

const menuMeta = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  truncate: true,
})

/** 明暗切换：S2 Provider colorScheme + data-theme 双通道 */
function ThemeToggleButton() {
  const theme = useThemeStore(s => s.theme)
  const toggle = useThemeStore(s => s.toggle)
  return (
    <ActionButton
      isQuiet
      aria-label={theme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
      onPress={toggle}
    >
      <ContrastIcon />
    </ActionButton>
  )
}

/** 顶栏全局搜索：按笔记标题 / 链接过滤，点击结果回到工作台阅读区 */
function GlobalSearch() {
  const navigate = useNavigate()
  const tasks = useTaskStore(s => s.tasks)
  const setCurrentTask = useTaskStore(s => s.setCurrentTask)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setExpanded(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [])

  const keyword = query.trim().toLowerCase()
  const results = useMemo(() => {
    if (!keyword) return []
    return tasks
      .filter(task => {
        const title = task.audioMeta?.title || task.formData?.video_url || ''
        return title.toLowerCase().includes(keyword)
      })
      .slice(0, 8)
  }, [keyword, tasks])

  const open = expanded && keyword.length > 0

  return (
    <div className={searchWrap} ref={wrapRef}>
      <SearchField
        aria-label="搜索笔记"
        placeholder="搜索笔记…"
        value={query}
        onChange={value => {
          setQuery(value)
          setExpanded(true)
        }}
      />
      {open ? (
        <div className={searchResults} onMouseDown={event => event.preventDefault()}>
          {results.length === 0 ? (
            <span className={searchEmpty}>没有匹配的笔记</span>
          ) : (
            results.map(task => {
              const title = task.audioMeta?.title || task.formData?.video_url || '未命名笔记'
              return (
                <button
                  key={task.id}
                  type="button"
                  className={`${searchResult} row-hover focus-ring`}
                  onClick={() => {
                    setCurrentTask(task.id)
                    setQuery('')
                    setExpanded(false)
                    navigate('/')
                  }}
                >
                  <span className={searchResultTitle}>{title}</span>
                </button>
              )
            })
          )}
        </div>
      ) : null}
    </div>
  )
}

/** 侧栏一项：使用 S2 SideNav，选中态/悬停/焦点/折叠行为由组件负责 */
function NavRow({ item }: { item: NavItem }) {
  const { key, label, Icon } = item
  return (
    <SideNavItem id={key} textValue={label} {...({ href: key } as object)}>
      <SideNavItemContent>
        <SideNavItemLink {...({ href: key } as object)}>
          <Icon />
          <Text>{label}</Text>
        </SideNavItemLink>
      </SideNavItemContent>
    </SideNavItem>
  )
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const enforced = useAuthStore(s => s.enforced)
  const signOut = useAuthStore(s => s.logout)
  const [navCollapsed, setNavCollapsed] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= 768,
  )

  // 按管理员身份过滤设置菜单。
  // 之前是静态数组直接 map，普通用户也能看到「用户管理/部署监控」，
  // 点进去才吃 403 —— 入口与后端权限模型不一致（审计结论 6）。
  // 注意：这里只做展示层收敛，真正的权限判定始终在后端 require_admin。
  const visibleSettingsNav = useMemo(
    () => SETTINGS_NAV.filter(item => !item.adminOnly || user?.is_superuser),
    [user?.is_superuser],
  )

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth <= 768) setNavCollapsed(true)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const activeKey = useMemo(() => {
    if (pathname === '/') return '/'
    if (
      pathname.startsWith('/settings/ai') ||
      pathname.startsWith('/settings/model') ||
      pathname.startsWith('/settings/transcriber')
    ) {
      return '/settings/ai'
    }
    const match = SETTINGS_NAV.find(item => pathname.startsWith(item.key))
    return match?.key ?? '/'
  }, [pathname])

  const [isTauri, setIsTauri] = useState(false)
  useEffect(() => {
    setIsTauri(typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window)
  }, [])

  const handleLogout = async () => {
    try {
      await signOut()
      toast.info('已退出登录')
      if (enforced) navigate('/login', { replace: true })
    } catch {
      toast.error('退出失败，请重试')
    }
  }

  return (
    <div className={shell}>
      <header className={`${topbar} app-frame-topbar`}>
        <Link to="/" className={brand} aria-label="OpenNoter 工作台">
          <img src={logo} alt="" width={20} height={20} className={logoMark} />
          <span className={brandName}>OpenNoter</span>
        </Link>

        <GlobalSearch />

        <div className={topbarActions}>
          {isTauri ? <BackendHealthIndicator /> : null}
          <ThemeToggleButton />
          {user ? (
            <MenuTrigger align="end" direction="bottom">
              {/*
                顶栏账户入口。ActionButton 的官方 anatomy 就是
                `<Icon /> or <Avatar />` + `<Text />`；S2 的样式里还专门有
                针对 [slot="avatar"] 的规则（去掉左右内边距、单列网格），
                所以头像放 slot="avatar" 即可，不必用 CSS 硬压组件默认值。
                我们没有用户照片，用首字母圆形头像代替 Avatar 的图片；
                容器默认/hover 状态完全交给 S2，不在 CSS 中固定底色。
              */}
              <ActionButton isQuiet aria-label={`${user.name || user.email} 的账户菜单`}>
                <span slot="avatar" className={avatar}>
                  {(user.name || user.email).slice(0, 1).toUpperCase()}
                </span>
              </ActionButton>
              <Menu onAction={key => key === 'logout' && handleLogout()}>
                <MenuSection>
                  <Header>
                    <div className={menuIdentity}>
                      <span className={menuName}>{user.name || user.email}</span>
                      <span className={menuMeta}>{user.email}</span>
                      <span className={menuMeta}>{user.is_superuser ? '管理员' : '普通用户'}</span>
                    </div>
                  </Header>
                </MenuSection>
                <MenuSection>
                  <MenuItem id="logout" textValue="退出登录">
                    <Leave />
                    <Text>退出登录</Text>
                  </MenuItem>
                </MenuSection>
              </Menu>
            </MenuTrigger>
          ) : (
            <Button variant="secondary" onPress={() => navigate('/login')}>
              登录
            </Button>
          )}
        </div>
      </header>

      <div className={body}>
        <SideNavPanel
          aria-label="主导航"
          isCollapsed={navCollapsed}
          onCollapsedChange={setNavCollapsed}
          styles={navPanel}
        >
          <div className={navInner}>
            <SideNav
              aria-label="主导航"
              selectedRoute={activeKey}
              styles={navFull}
              UNSAFE_className="app-nav"
            >
              <SideNavSection aria-label="工作台">
                <SideNavItem id="/" textValue="工作台" {...({ href: '/' } as object)}>
                  <SideNavItemContent>
                    <SideNavItemLink {...({ href: '/' } as object)}>
                      <HomeIcon />
                      <Text>工作台</Text>
                    </SideNavItemLink>
                  </SideNavItemContent>
                </SideNavItem>
              </SideNavSection>

              <SideNavSection aria-label="设置">
                {visibleSettingsNav.map(item => (
                  <NavRow key={item.key} item={item} />
                ))}
              </SideNavSection>
            </SideNav>
          </div>
        </SideNavPanel>

        <main className={`${content} app-frame-content`}>{children}</main>
      </div>
    </div>
  )
}
