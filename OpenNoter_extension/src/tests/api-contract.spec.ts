import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { generateNote, getTaskStatus, setDownloaderCookie } from '../logic/api'

const state = vi.hoisted(() => ({ value: { backendUrl: 'https://notes.example', apiKey: 'one' } }))
vi.mock('../logic/storage', () => ({ settings: state, settingsReady: Promise.resolve() }))

const fetcher = vi.fn()
const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ code: 0, data }) })
beforeEach(() => {
  state.value.apiKey = 'one'
  fetcher.mockReset()
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

it('submits the current note contract with server-selected defaults', async () => {
  fetcher.mockResolvedValue(ok({ task_id: 'task' }))
  await generateNote({ video_url: 'https://youtu.be/one', platform: 'youtube', quality: 'medium', model_name: '', provider_id: '' })
  expect(fetcher.mock.calls[0][0]).toBe('https://notes.example/api/notes')
  const body = JSON.parse(fetcher.mock.calls[0][1].body)
  expect(body.url).toBe('https://youtu.be/one')
  expect(body).not.toHaveProperty('provider_id')
  expect(body).not.toHaveProperty('model_name')
})
it('uses the current task endpoint and rejects an old account response', async () => {
  let resolve!: (response: unknown) => void
  fetcher.mockImplementation(() => new Promise((r) => {
    resolve = r
  }))
  const pending = getTaskStatus('task')
  await Promise.resolve()
  expect(fetcher.mock.calls[0][0]).toBe('https://notes.example/api/tasks/task')
  state.value.apiKey = 'two'
  resolve(ok({ status: 'SUCCESS' }))
  await expect(pending).rejects.toMatchObject({ status: 409 })
})
it('sends personal cookies to the account API when cookie BYOK is enabled', async () => {
  fetcher.mockResolvedValueOnce(ok({ cookie_mode: 'optional' })).mockResolvedValueOnce(ok({}))
  await setDownloaderCookie('bilibili', 'SESSDATA=test')
  expect(fetcher.mock.calls[1][0]).toBe('https://notes.example/api/me/cookies/bilibili')
  expect(fetcher.mock.calls[1][1].method).toBe('PUT')
})
it('uses the shared cookie API only under shared policy', async () => {
  fetcher.mockResolvedValueOnce(ok({ cookie_mode: 'shared' })).mockResolvedValueOnce(ok({}))
  await setDownloaderCookie('bilibili', 'SESSDATA=test')
  expect(fetcher.mock.calls[1][0]).toBe('https://notes.example/api/update_downloader_cookie')
})
