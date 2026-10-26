import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { v4 as uuidv4 } from 'uuid'
import { del, get, set } from 'idb-keyval'
import type { AudioMeta, MarkdownVersion, Task, TaskFormData, TaskStatus, Transcript } from '@/types'

const emptyTranscript = (): Transcript => ({
  full_text: '',
  language: '',
  raw: null,
  segments: [],
})

const emptyAudioMeta = (): AudioMeta => ({
  cover_url: '',
  duration: 0,
  file_path: '',
  platform: '',
  raw_info: null,
  title: '',
  video_id: '',
})

/** 旧版本把 markdown 存成字符串；统一收敛成版本数组 */
function normalizeMarkdown(task: any): MarkdownVersion[] {
  const raw = task?.markdown
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw.trim()) {
    return [
      {
        ver_id: `${task.id}-legacy`,
        content: raw,
        style: task?.formData?.style || '',
        model_name: task?.formData?.model_name || '',
        created_at: task?.createdAt || new Date().toISOString(),
      },
    ]
  }
  return []
}

function normalizeTask(task: any): Task {
  return {
    id: task.id,
    status: task.status === 'FAILD' ? 'FAILED' : task.status,
    markdown: normalizeMarkdown(task),
    transcript: task.transcript ?? emptyTranscript(),
    audioMeta: task.audioMeta ?? emptyAudioMeta(),
    createdAt: task.createdAt ?? new Date().toISOString(),
    formData: {
      ...task.formData,
      format: Array.isArray(task.formData?.format) ? task.formData.format : [],
    },
  }
}

interface TaskState {
  tasks: Task[]
  currentTaskId: string | null
  /**
   * 从服务端同步任务列表。
   *
   * 历史列表原先只存浏览器 IndexedDB：换浏览器/设备就看不到，清缓存就丢。
   * 现在以服务端为准（它按 user_id 做归属隔离），本地只作离线缓存：
   *   - 拉到服务端已有、本地没有的任务 → 补一个占位条目（正文点开再拉）
   *   - 服务端已删除、本地还在的 → 从本地移除
   *   - 本地正在进行中（PENDING/...）的任务 → 保留，避免刚提交就被覆盖
   */
  syncFromServer: () => Promise<void>
  syncing: boolean
  lastSyncedAt: string | null
  addPendingTask: (taskId: string, formData: TaskFormData) => void
  updateTask: (id: string, patch: Partial<Omit<Task, 'id'>>) => void
  markSuccess: (
    id: string,
    payload: { markdown: string; transcript: Transcript; audioMeta: AudioMeta },
  ) => void
  removeTask: (id: string) => void
  /**
   * 重试一个失败的任务。
   *
   * 后端 `POST /api/notes` 支持传 `task_id` 复用同一个任务 ID（见
   * `_submit_note_task` 的「重试模式」），所以这里不需要新建任务，
   * 直接把原任务的 formData 重新提交即可 —— 好处是历史列表里那条记录
   * 原地复活，不会多出一条重复项。
   *
   * 为什么需要它：任务跑在进程内线程池里，服务重启/崩溃会让进行中的任务
   * 被标记为 FAILED（`task_interrupt`）。用户之前只能重新填一遍表单，
   * 这里给一个一键重试入口。
   */
  retryTask: (id: string) => Promise<void>
  /**
   * 补拉一个「服务端判定成功、但本地没有正文」的任务。
   *
   * `syncFromServer` 为远端任务建的是占位条目（`markdown: []`）。若该任务状态已是
   * SUCCESS，`useTaskPolling` 的 `isRunningStatus` 过滤会把它排除，正文就永远拉不到，
   * `NotePreview` 只能渲染空白 —— 这是「换浏览器 / 清缓存后，历史笔记点开是白的」的根因。
   *
   * 触发条件（三条同时满足，见实现）：本地有该任务、`markdown` 为空、`status === 'SUCCESS'`。
   * **不依赖 `has_result`**：该字段目前在 `list_tasks_for_user` 的 DB 分支不存在，
   * 只有文件扫描回退分支才产出（恒为 `undefined` ⇒ 判断恒假）。调用方负责次数上限。
   *
   * @returns 是否真的补到了正文
   */
  hydrateTask: (id: string) => Promise<boolean>
  setCurrentTask: (id: string | null) => void
  reset: () => void
}

export const useTaskStore = create<TaskState>()(
  persist(
    (set) => ({
      tasks: [],
      currentTaskId: null,
      syncing: false,
      lastSyncedAt: null,

      syncFromServer: async () => {
        const { listTasks } = await import('@/services/note')
        set({ syncing: true })
        try {
          const res = await listTasks({ limit: 200 })
          const remote = res?.items ?? []
          set(state => {
            const localById = new Map(state.tasks.map(t => [t.id, t]))
            const remoteIds = new Set(remote.map(r => r.task_id))
            const running = new Set([
              'PENDING',
              'PARSING',
              'DOWNLOADING',
              'TRANSCRIBING',
              'SUMMARIZING',
              'FORMATTING',
              'SAVING',
            ])

            const merged: Task[] = []

            // 1) 服务端有的：沿用本地已有内容，否则建占位条目
            for (const r of remote) {
              const local = localById.get(r.task_id)
              if (local) {
                merged.push({
                  ...local,
                  status: (r.status as TaskStatus) || local.status,
                  // 同步服务端的 message：失败原因要能跨刷新保留
                  // （否则用户刷新页面后，失败页只剩一句泛泛提示）。
                  message: r.message ?? local.message,
                })
              } else {
                merged.push({
                  id: r.task_id,
                  status: (r.status as TaskStatus) || 'PENDING',
                  message: r.message ?? undefined,
                  markdown: [],
                  transcript: emptyTranscript(),
                  audioMeta: emptyAudioMeta(),
                  createdAt: r.updated_at || new Date().toISOString(),
                  formData: {
                    video_url: '',
                    platform: '',
                    quality: 'medium',
                    format: [],
                    model_name: '',
                    provider_id: '',
                    style: '',
                    extras: '',
                  } as TaskFormData,
                })
              }
            }

            // 2) 本地有、服务端没有的：正在跑的保留，已结束的移除
            for (const t of state.tasks) {
              if (remoteIds.has(t.id)) continue
              if (running.has(t.status)) merged.push(t)
            }

            return { tasks: merged, syncing: false, lastSyncedAt: new Date().toISOString() }
          })
        } catch {
          // 离线/未登录：保留本地缓存，不打断使用
          set({ syncing: false })
        }
      },

      addPendingTask: (taskId, formData) =>
        set(state => ({
          tasks: [
            {
              id: taskId,
              status: 'PENDING',
              markdown: [],
              transcript: emptyTranscript(),
              audioMeta: { ...emptyAudioMeta(), platform: formData.platform },
              createdAt: new Date().toISOString(),
              formData,
            },
            ...state.tasks,
          ],
          currentTaskId: taskId,
        })),

      updateTask: (id, patch) =>
        set(state => ({
          tasks: state.tasks.map(task => (task.id === id ? { ...task, ...patch } : task)),
        })),

      markSuccess: (id, { markdown, transcript, audioMeta }) =>
        set(state => ({
          tasks: state.tasks.map(task => {
            if (task.id !== id) return task
            const version: MarkdownVersion = {
              ver_id: `${task.id}-${uuidv4()}`,
              content: markdown,
              style: task.formData.style || '',
              model_name: task.formData.model_name || '',
              created_at: new Date().toISOString(),
            }
            const sameContent = task.markdown.some(v => v.content === markdown)
            return {
              ...task,
              status: 'SUCCESS',
              markdown: sameContent ? task.markdown : [version, ...task.markdown],
              transcript,
              audioMeta: { ...task.audioMeta, ...audioMeta },
            }
          }),
        })),

      removeTask: id => {
        set(state => ({
          tasks: state.tasks.filter(t => t.id !== id),
          currentTaskId: state.currentTaskId === id ? null : state.currentTaskId,
        }))
        // 动态 import 避免 store ↔ service 的循环依赖
        import('@/services/note')
          .then(({ deleteTask }) => deleteTask(id).catch(() => undefined))
          .catch(() => undefined)
      },

      retryTask: async id => {
        const task = useTaskStore.getState().tasks.find(t => t.id === id)
        if (!task) return
        // 先乐观置为 PENDING，避免用户连点两次
        set(state => ({
          tasks: state.tasks.map(t => (t.id === id ? { ...t, status: 'PENDING' as TaskStatus } : t)),
        }))
        try {
          const { generateNote } = await import('@/services/note')
          await generateNote({ ...task.formData, task_id: id })
        } catch (err) {
          // 提交失败：退回 FAILED，让用户能看到并再次重试
          set(state => ({
            tasks: state.tasks.map(t =>
              t.id === id ? { ...t, status: 'FAILED' as TaskStatus } : t
            ),
          }))
          throw err
        }
      },

      setCurrentTask: id => set({ currentTaskId: id }),

      hydrateTask: async id => {
        const task = useTaskStore.getState().tasks.find(t => t.id === id)
        // 已经有正文的不补（本地缓存的正常路径，零额外请求）；
        // 状态不是 SUCCESS 的交给常规轮询，不在这里抢。
        if (!task || task.markdown.length > 0 || task.status !== 'SUCCESS') return false

        // 动态 import 与 retryTask / removeTask 同范式，避免 store ↔ service 循环依赖
        const { getTaskStatus } = await import('@/services/note')
        try {
          const res = await getTaskStatus(id)
          if (res?.status === 'SUCCESS' && res.result?.markdown) {
            // 走既有的 markSuccess：它按内容去重（`markdown.some(v => v.content === ...)`），
            // 重复补拉不会产生重复版本；markdown 结构仍是 MarkdownVersion[]，持久化格式不变。
            useTaskStore.getState().markSuccess(id, {
              markdown: res.result.markdown,
              transcript: res.result.transcript ?? task.transcript,
              audioMeta: res.result.audio_meta ?? task.audioMeta,
            })
            return true
          }
          // SUCCESS 但拿不到正文：结果文件确实不存在（F4 的 SUCCESS 早于落盘窗口、
          // 或已被删除）。退回 SAVING 让轮询再等一轮，而不是永久空白。
          // 次数上限由调用方（useTaskPolling）控制，这里不做循环。
          if (res?.status === 'SUCCESS') {
            useTaskStore.getState().updateTask(id, { status: 'SAVING' as TaskStatus })
          }
          return false
        } catch {
          // 离线 / 鉴权失败：保持占位条目，下一轮同步再试（调用方有次数上限）
          return false
        }
      },

      reset: () => set({ tasks: [], currentTaskId: null }),
    }),
    {
      name: 'task-storage',
      version: 2,
      migrate: (persisted: any) => {
        const state = persisted as { tasks?: any[]; currentTaskId?: string | null }
        return {
          tasks: (state?.tasks ?? []).map(normalizeTask),
          currentTaskId: state?.currentTaskId ?? null,
        }
      },
      storage: createJSONStorage(() => ({
        getItem: async (name: string) => (await get(name)) ?? null,
        setItem: async (name: string, value: string) => {
          await set(name, value)
        },
        removeItem: async (name: string) => {
          await del(name)
        },
      })),
    },
  ),
)

export const selectCurrentTask = (state: TaskState) =>
  state.tasks.find(task => task.id === state.currentTaskId) ?? null

export const isRunningStatus = (status: TaskStatus) =>
  status !== 'SUCCESS' && status !== 'FAILED'
