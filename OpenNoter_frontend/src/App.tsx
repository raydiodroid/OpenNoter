import { lazy, Suspense, useEffect, useMemo, type ReactNode } from 'react'
import {
  BrowserRouter,
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useHref,
  useNavigate,
} from 'react-router-dom'
import { Provider, ToastContainer } from '@react-spectrum/s2'

import { useTaskPolling } from '@/hooks/useTaskPolling'
import { useCheckBackend } from '@/hooks/useCheckBackend'
import { useAuthStore } from '@/store/authStore'
import { useThemeStore } from '@/store/themeStore'
import AppShell from '@/components/AppShell'
import BackendInitDialog from '@/components/BackendInitDialog'
import LoadingScreen from '@/components/LoadingScreen'
import StartupBanner from '@/components/SystemDiagnostic/StartupBanner'
import WorkbenchPage from '@/pages/WorkbenchPage'
import { EmptyState, Page } from '@/components/Page'
import ErrorBoundary from '@/components/ErrorBoundary'

const OnboardingPage = lazy(() => import('@/pages/OnboardingPage'))
const LoginPage = lazy(() => import('@/pages/LoginPage'))
const AISettingsPage = lazy(() => import('@/pages/settings/AISettingsPage'))
const DownloaderPage = lazy(() => import('@/pages/settings/DownloaderPage'))
const KeysPage = lazy(() => import('@/pages/settings/KeysPage'))
const UsersPage = lazy(() => import('@/pages/settings/UsersPage'))
const MonitorPage = lazy(() => import('@/pages/settings/MonitorPage'))
const AboutPage = lazy(() => import('@/pages/settings/AboutPage'))
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'))

// `Loading` 统一用 `LoadingScreen`：视觉与 index.html 内联的启动骨架一致，
// 所以「HTML 骨架 → React 骨架」切换时看不出跳变。
//
// 之前这里是个裸 ProgressCircle（全屏居中），与骨架屏风格不连续 ——
// 用户会看到「骨架卡片 → 忽然只剩一个转圈」的突兀切换。
function Loading({ hintText }: { hintText?: string } = {}) {
  return <LoadingScreen hintText={hintText} />
}

/**
 * 认证守卫：仅在后端开启强制认证时拦截。
 *
 * ## 为什么这里不用看内存 token（F1 的「启动门」结论）
 *
 * 桌面端凭据只在内存（`lib/api.ts` 的 `setAccessToken`），进程退出即丢，
 * 重启后必然没有 token。但 `init()` 在拿到 `ready` 之前会先跑一次 `getMe()`：
 * 桌面端无 token ⇒ 该请求 401 ⇒ `user` 为 null。而 `ready === false` 期间
 * 整个路由树都渲染 `<Loading />`，**主界面从未被渲染过**，所以「启动时
 * enforced && !token」天然就落在 `<Navigate to="/login" />` 上，不需要额外判定。
 *
 * 反过来说，也不能把 `hasCredentials()` 加进来当判据：它对 Web 端恒为 false
 * （Web 走同源 HttpOnly Cookie，token 从不下发到内存），加进来只会写出一个
 * 恒假的额外条件，让人误以为 Web 端靠 token 判定登录态。
 */
function AuthGuard({ children }: { children: ReactNode }) {
  const ready = useAuthStore(s => s.ready)
  const enforced = useAuthStore(s => s.enforced)
  const user = useAuthStore(s => s.user)
  const location = useLocation()

  if (!ready) return <Loading />
  if (enforced && !user) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

/** 桌面端首启引导：仅 Tauri 环境拦截 */
function OnboardingGuard({ children }: { children: ReactNode }) {
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  if (!isTauri) return <>{children}</>
  if (localStorage.getItem('opennoter-onboarded') !== '1') {
    return <Navigate to="/onboarding" replace />
  }
  return <>{children}</>
}

/**
 * 管理员守卫：非管理员直接看到「无权访问」，而不是点进去吃一个 403 报错。
 *
 * 后端 /monitor 等接口对普通用户返回 403；前端若把入口和路由都敞开，
 * 普通用户会先看到菜单项、再看到一条红条 + 空白页，是权限模型不一致。
 * 这里只做**展示层**收敛，真正的权限判定始终在后端 require_admin。
 */
function AdminGuard({ children }: { children: ReactNode }) {
  const ready = useAuthStore(s => s.ready)
  const user = useAuthStore(s => s.user)
  const enforced = useAuthStore(s => s.enforced)

  if (!ready) return <Loading />
  // F52：`user === null` 不能直接放行。
  //
  // 旧判断是 `user && !user.is_superuser` —— 当 `user` 为 null（未登录）时
  // 条件为假 ⇒ 直接渲染子节点。在 `AUTH_ENFORCE=0` 的部署里这会让普通访客
  // 看到管理员页面骨架（后端仍会 403，结果是一屏红条 + 空白，比直接说
  // 「无权访问」更糟）；若哪天后端某个管理接口漏挂 require_admin，前端这层
  // 就成了最后一层"看起来放过了"的假象。
  //
  // 正确语义：**只有确认是管理员才放行**，其余（未登录 / 非管理员）一律拦住。
  // 未登录时由外层 AuthGuard 负责跳登录页，这里只兜住"取不到用户"的情形。
  const isAdmin = Boolean(user?.is_superuser)
  if (!isAdmin) {
    // 开启了强制认证但没取到用户 → 交给 AuthGuard 处理（它会 Navigate 到 /login）。
    // 这里返回 Loading 避免闪一下「无权访问」。
    if (enforced && !user) return <Loading />

    return (
      <Page>
        <EmptyState title="无权访问" illustration={null}>
          该页面仅对管理员开放。如需权限，请联系管理员。
        </EmptyState>
      </Page>
    )
  }
  return <>{children}</>
}

/**
 * 把当前路由当作 ErrorBoundary 的 resetKey：切页时自动清掉上一页的错误态，
 * 否则用户在错误页里点侧栏也只会看到同一个错误。
 */
function RouteBoundary({ children }: { children: ReactNode }) {
  const location = useLocation()
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>
}

/**
 * S2 Provider 需要拿到 react-router 的 navigate 才能做客户端跳转。
 *
 * ## 为什么必须同时提供 `useHref`（B1）
 *
 * S2 的 `SideNavItemLink` / `Link` 最终渲染成裸 `<a href>`。若 `Router` 只给
 * `navigate` 而不给 `useHref`，`RouterProvider` 会退化成恒等映射 `href => href`：
 *
 *   - **桌面端（HashRouter）**：`href="/settings/ai"` 是根相对路径，点击会导航到
 *     `tauri://localhost/settings/ai` —— 跳出 hash 路由，整个 SPA 白屏
 *     （与 F11 同一症状家族，但触发点是侧栏而非 401）。
 *   - **Web（BrowserRouter）**：整页刷新，丢失内存态（桌面端 token 就在内存里）。
 *
 * react-router 的 `useHref` 正好负责「应用内路径 → 正确 href 形态」的翻译
 * （hash 模式给 `#/path`，浏览器模式给 `/path`），直接透传即可。
 * **不要**再手动拼 `#`，否则 hash 模式下会变成 `##/x`。
 */
function SpectrumRoot({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const hrefFor = useHref
  const theme = useThemeStore(s => s.theme)
  const router = useMemo(
    () => ({
      navigate: (path: string, options?: { replace?: boolean }) =>
        navigate(path, { replace: options?.replace }),
      // 让 S2 的 <a href> 在 hash 路由下也指向 #/path，不跳出 SPA
      useHref: (to: string) => hrefFor(to),
    }),
    [navigate, hrefFor],
  )

  return (
    <Provider locale="zh-CN" colorScheme={theme} router={router}>
      {children}
      <ToastContainer placement="bottom end" />
    </Provider>
  )
}

function AppRoutes() {
  useTaskPolling(3000)

  const initialize = useAuthStore(s => s.init)
  useEffect(() => {
    initialize()
  }, [initialize])

  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route
          path="/*"
          element={
            <AuthGuard>
              <OnboardingGuard>
                <AppShell>
                  <Suspense fallback={<Loading />}>
                    {/* 错误边界放在 AppShell 内部：页面崩了侧栏和顶栏仍然可用，
                        而不是整棵树被卸载成白屏（历史缺陷见 _audit/REPORT.md P0-2） */}
                    <RouteBoundary>
                      <Routes>
                        <Route index element={<WorkbenchPage />} />
                        <Route path="/settings/ai" element={<AISettingsPage />} />
                        <Route path="/settings/ai/new" element={<AISettingsPage isCreate />} />
                        <Route path="/settings/ai/:id" element={<AISettingsPage />} />
                        <Route
                          path="/settings/transcriber"
                          element={<AISettingsPage tab="transcriber" />}
                        />
                        {/* 旧链接兼容 */}
                        <Route path="/settings/model" element={<Navigate to="/settings/ai" replace />} />
                        <Route
                          path="/settings/model/new"
                          element={<Navigate to="/settings/ai/new" replace />}
                        />
                        <Route path="/settings/download" element={<DownloaderPage />} />
                        <Route path="/settings/download/:id" element={<DownloaderPage />} />
                        <Route path="/settings/keys" element={<KeysPage />} />
                        <Route
                          path="/settings/users"
                          element={
                            <AdminGuard>
                              <UsersPage />
                            </AdminGuard>
                          }
                        />
                        <Route
                          path="/settings/monitor"
                          element={
                            <AdminGuard>
                              <MonitorPage />
                            </AdminGuard>
                          }
                        />
                        <Route path="/settings/about" element={<AboutPage />} />
                        {/* 旧链接兼容：历史已并入工作台 */}
                        <Route path="/history" element={<Navigate to="/" replace />} />
                        <Route path="*" element={<NotFoundPage />} />
                      </Routes>
                    </RouteBoundary>
                  </Suspense>
                </AppShell>
              </OnboardingGuard>
            </AuthGuard>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  )
}

export default function App() {
  const { loading, initialized, failed, lastError, retry } = useCheckBackend()
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  const Router = isTauri ? HashRouter : BrowserRouter

  if (!initialized) {
    // 启动期渲染什么？
    //
    // 旧实现只渲染 `<StartupBanner />`（很薄的一条横幅），其余是空白 ——
    // Web 部署下探测只要几毫秒，用户会看到「白屏 → 内容」的闪烁。
    //
    // 现在：
    //   - 桌面端：照旧弹 Modal（sidecar 解压 5-15s，必须给明确反馈）
    //   - Web 端：先渲染与正常页面同底的 `<Loading />`（全屏居中的 spinner），
    //     后端快的话用户看到的是「骨架 → 内容」，不是「白屏 → 内容」。
    //
    // `BackendInitDialog` 自己在 Web 端有 300ms 宽限期，
    // 所以后端正常时它**不会**盖上来（见该组件的说明）。
    return (
      <>
        <StartupBanner />
        {/*
          Web 端：渲染与最终页面同构的骨架屏（不再白屏一闪）。
          桌面端不需要 —— 它的 Modal 会覆盖在上层，再垫一层骨架纯属浪费
          （而且 sidecar 解压期本来就该让用户看 Modal 里的进度说明）。
        */}
        {isTauri ? null : <Loading hintText={loading ? '正在连接后端服务…' : '正在启动…'} />}
        <BackendInitDialog open={loading} failed={failed} lastError={lastError} onRetry={retry} />
      </>
    )
  }

  return (
    <Router>
      <SpectrumRoot>
        <StartupBanner />
        <AppRoutes />
      </SpectrumRoot>
    </Router>
  )
}
