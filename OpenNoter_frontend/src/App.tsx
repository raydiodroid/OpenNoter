import { lazy, Suspense, useEffect, useMemo, type ReactNode } from 'react'
import {
  BrowserRouter,
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'
import { Provider, ProgressCircle, ToastContainer } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { useTaskPolling } from '@/hooks/useTaskPolling'
import { useCheckBackend } from '@/hooks/useCheckBackend'
import { useAuthStore } from '@/store/authStore'
import { useThemeStore } from '@/store/themeStore'
import AppShell from '@/components/AppShell'
import BackendInitDialog from '@/components/BackendInitDialog'
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

const centered = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: 'screen',
})

function Loading() {
  return (
    <div className={centered}>
      <ProgressCircle aria-label="加载中" isIndeterminate />
    </div>
  )
}

/** 认证守卫：仅在后端开启强制认证时拦截 */
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

  if (!ready) return <Loading />
  if (user && !user.is_superuser) {
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

/** S2 Provider 需要拿到 react-router 的 navigate 才能做客户端跳转 */
function SpectrumRoot({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const theme = useThemeStore(s => s.theme)
  const router = useMemo(
    () => ({
      navigate: (path: string, options?: { replace?: boolean }) =>
        navigate(path, { replace: options?.replace }),
    }),
    [navigate],
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
    return (
      <>
        <StartupBanner />
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
