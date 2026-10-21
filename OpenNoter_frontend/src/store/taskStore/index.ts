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
