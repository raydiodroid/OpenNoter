import { beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ cache: new Map<string, string>(), list: vi.fn(), read: vi.fn(), remove: vi.fn() }))
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn() } }))
vi.mock('idb-keyval', () => ({ get: async (k: string) => mocks.cache.get(k), set: async (k: string, v: string) => { mocks.cache.set(k, v) }, del: async (k: string) => { mocks.cache.delete(k) } }))
vi.mock('@/services/note', () => ({ listTasks: mocks.list, getTaskStatus: mocks.read, deleteTask: mocks.remove }))
import { useTaskStore as store } from './index'
import { setSessionIdentity } from '@/lib/sessionScope'
import type { TaskFormData } from '@/types'

const form = { video_url: 'https://youtube.com/watch?v=one', quality: 'medium', format: [] } as unknown as TaskFormData
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
function add(id: string) {
  store.getState().addPendingTask(id, form)
  store.getState().markSuccess(id, { markdown: 'private text', transcript: {} as never, audioMeta: {} as never })
}
beforeEach(async () => {
  setSessionIdentity(null)
  mocks.cache.clear()
  mocks.list.mockReset()
  mocks.read.mockReset()
  setSessionIdentity(1)
  await tick()
})
it('clears visible data when accounts change and restores only that account cache', async () => {
  add('one')
  await tick()
  setSessionIdentity(2)
  expect(store.getState().tasks).toEqual([])
  await tick()
  expect(store.getState().tasks).toEqual([])
  add('two')
  await tick()
  setSessionIdentity(1)
  await tick()
  expect(store.getState().tasks.map(t => t.id)).toEqual(['one'])
})
it('fetches every page and preserves browser-only versions', async () => {
  for (let i = 0; i < 201; i++) add(`t${i}`)
  mocks.list.mockImplementation(async ({ offset }: { offset: number }) => ({
    items: Array.from({ length: offset === 0 ? 200 : 1 }, (_, i) => ({ task_id: `t${offset + i}`, status: 'SUCCESS' })), total: 201,
  }))
  await store.getState().syncFromServer()
  expect(mocks.list).toHaveBeenCalledTimes(2)
  expect(store.getState().tasks).toHaveLength(201)
  expect(store.getState().tasks.find(t => t.id === 't200')?.markdown[0].content).toBe('private text')
})
it('ignores a list response belonging to the previous account', async () => {
  let finish!: (v: unknown) => void
  mocks.list.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const pending = store.getState().syncFromServer()
  await tick()
  setSessionIdentity(2)
  finish({ items: [{ task_id: 'secret-one', status: 'SUCCESS' }], total: 1 })
  await pending
  expect(store.getState().tasks).toEqual([])
})
it('does not import legacy cache with unknown ownership', async () => {
  mocks.cache.set('task-storage', JSON.stringify({ state: { tasks: [{ id: 'unknown-secret' }] }, version: 2 }))
  setSessionIdentity(3)
  await tick()
  expect(store.getState().tasks).toEqual([])
})
it('turns an unavailable completed artifact into a visible terminal failure', async () => {
  store.getState().addPendingTask('lost', form)
  store.getState().updateTask('lost', { status: 'SUCCESS' })
  mocks.read.mockResolvedValue({ status: 'FAILED', message: '结果缺失' })
  await store.getState().hydrateTask('lost')
  expect(store.getState().tasks[0].status).toBe('FAILED')
})
it('only removes explicit server tombstones, retaining entries absent from a changing page', async () => {
  add('deleted')
  add('unseen')
  mocks.list.mockResolvedValue({ items: [{ task_id: 'deleted', deleted: true }], total: 1 })
  await store.getState().syncFromServer()
  expect(store.getState().tasks.map(t => t.id)).toEqual(['unseen'])
})
it('refreshes a new server attempt while preserving previous local versions', async () => {
  add('regenerated')
  store.getState().updateTask('regenerated', { attemptId: 'old' })
  mocks.list.mockResolvedValue({ items: [{ task_id: 'regenerated', attempt_id: 'new', status: 'SUCCESS' }], total: 1 })
  await store.getState().syncFromServer()
  expect(store.getState().tasks[0].needsRefresh).toBe(true)
  mocks.read.mockResolvedValue({ status: 'SUCCESS', attempt_id: 'new', result: { markdown: 'new text' } })
  await store.getState().hydrateTask('regenerated')
  expect(store.getState().tasks[0].markdown.map(v => v.content)).toEqual(['new text', 'private text'])
  expect(store.getState().tasks[0].needsRefresh).toBe(false)
})
