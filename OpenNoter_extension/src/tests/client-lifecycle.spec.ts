import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, connectionScope } from '../logic/client'
import { createTaskPoller } from '../logic/task-poller'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
it('uses the supplied connection snapshot in a worker without Vue storage', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ code: 0, data: { task_id: 'x' } }) })
  vi.stubGlobal('fetch', fetch)
  const data = await apiRequest({ backendUrl: 'https://host.example/', apiKey: 'owned-key' }, '/api/notes', { method: 'POST' })
  expect(data).toEqual({ task_id: 'x' })
  expect(fetch.mock.calls[0][0]).toBe('https://host.example/api/notes')
  expect(fetch.mock.calls[0][1].headers['x-api-key']).toBe('owned-key')
})
it('preserves HTTP authorization errors for terminal polling decisions', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ msg: 'denied' }) }))
  await expect(apiRequest({ backendUrl: 'https://host.example', apiKey: '' }, '/api/tasks/x')).rejects.toMatchObject({ status: 403 })
})
it.each([401, 403, 404, 409, 410])('stops polling on HTTP %s', async (status) => {
  vi.useFakeTimers()
  const read = vi.fn().mockRejectedValue(new ApiError('terminal', status))
  const poller = createTaskPoller(read, vi.fn(), vi.fn())
  poller.start('x')
  await vi.advanceTimersByTimeAsync(20000)
  expect(read).toHaveBeenCalledTimes(1)
  poller.stop()
})
it('ignores a response that completes after selection changes', async () => {
  vi.useFakeTimers()
  let resolve!: (value: any) => void
  const read = vi.fn().mockImplementationOnce(() => new Promise((r) => {
    resolve = r
  })).mockResolvedValue({ status: 'SUCCESS' })
  const result = vi.fn()
  const poller = createTaskPoller(read, result, vi.fn())
  poller.start('old')
  poller.start('new')
  resolve({ status: 'PENDING' })
  await vi.advanceTimersByTimeAsync(10000)
  expect(result).toHaveBeenCalledTimes(1)
  expect(result.mock.calls[0][0]).toBe('new')
  expect(read).toHaveBeenCalledTimes(2)
  poller.stop()
})
it('gives each endpoint and credential a different opaque cache scope', async () => {
  // jsdom does not implement SubtleCrypto; use Node's native implementation.
  const { webcrypto } = await import('node:crypto')
  vi.stubGlobal('crypto', webcrypto)
  const one = await connectionScope({ backendUrl: 'https://one.example', apiKey: 'a' })
  const two = await connectionScope({ backendUrl: 'https://one.example', apiKey: 'b' })
  const three = await connectionScope({ backendUrl: 'https://two.example', apiKey: 'a' })
  expect(new Set([one, two, three]).size).toBe(3)
  expect(one).toMatch(/^[0-9a-f]{64}$/)
})
