import { beforeEach, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ logout: vi.fn(), me: vi.fn(), config: vi.fn() }))
vi.mock('@/services/auth', () => ({ getMe: api.me, getAuthConfig: api.config, login: vi.fn(), logout: api.logout }))
vi.mock('@/lib/api', () => ({ getAccessToken: vi.fn(), setAccessToken: vi.fn(), setUnauthorizedHandler: vi.fn() }))
import { useAuthStore as store } from './index'
import { sessionScope, setSessionIdentity } from '@/lib/sessionScope'

beforeEach(() => {
  vi.stubGlobal('window', {})
  api.logout.mockReset().mockResolvedValue(undefined)
  api.me.mockReset()
  api.config.mockReset()
  store.setState({ enforced: true, user: { id: 1 } as never })
  setSessionIdentity(1)
})
it('restores anonymous operation only after a successful logout on a public server', async () => {
  store.setState({ enforced: false })
  await store.getState().logout()
  expect(sessionScope()).toMatch(/:anonymous$/)
  expect(store.getState().user).toBeNull()
})
it('keeps caches locked when server logout fails', async () => {
  store.setState({ enforced: false })
  api.logout.mockRejectedValue(new Error('offline'))
  await expect(store.getState().logout()).rejects.toThrow('offline')
  expect(sessionScope()).toBeNull()
})
it('does not restore an old identity from a refresh that completes after logout', async () => {
  let finish!: (value: unknown) => void
  api.me.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const refresh = store.getState().refresh()
  await store.getState().logout()
  finish({ id: 1 })
  await refresh
  expect(sessionScope()).toBeNull()
  expect(store.getState().user).toBeNull()
})
it('fails closed when authentication configuration cannot be loaded', async () => {
  api.config.mockRejectedValue(new Error('offline'))
  await store.getState().init()
  expect(store.getState().enforced).toBe(true)
  expect(sessionScope()).toBeNull()
})
