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
                merged.push({ ...local, status: (r.status as TaskStatus) || local.status })
              } else {
                merged.push({
                  id: r.task_id,
                  status: (r.status as TaskStatus) || 'PENDING',
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

      setCurrentTask: id => set({ currentTaskId: id }),

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
