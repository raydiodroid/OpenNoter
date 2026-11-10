import { computed, ref, watch } from 'vue'
import { connectionKey, connectionScope } from './client'
import type { Settings, TaskRecord } from './types'
import { DEFAULT_SETTINGS, MAX_TASKS, SETTINGS_KEY, TASKS_KEY } from './constants'
import { useWebExtensionStorage } from '~/composables/useWebExtensionStorage'

export { DEFAULT_BACKEND_URL, DEFAULT_SETTINGS, MAX_TASKS } from './constants'

// 全局共享设置（popup / options / sidepanel 三个 Vue 上下文都读这一份）
// 注意：background service worker 不要 import 这个文件，改用 chrome.storage 直读
export const { data: settings, dataReady: settingsReady } = useWebExtensionStorage<Settings>(
  SETTINGS_KEY,
  DEFAULT_SETTINGS,
  { mergeDefaults: true },
)

const { data: allTasks, dataReady: allTasksReady } = useWebExtensionStorage<TaskRecord[]>(
  TASKS_KEY,
  [],
)

export const activeScope = ref<string | null>(null)
let scopeVersion = 0
async function selectScope() {
  const version = ++scopeVersion
  activeScope.value = null
  const scope = await connectionScope({ ...settings.value })
  if (version === scopeVersion)
    activeScope.value = scope
}
export const tasksReady = Promise.all([settingsReady, allTasksReady]).then(selectScope)
watch(() => connectionKey(settings.value), selectScope, { flush: 'sync' })
export const tasks = computed(() => (allTasks.value || []).filter(t => activeScope.value && t.scope === activeScope.value))

export function upsertTask(record: TaskRecord) {
  const scope = activeScope.value
  if (!scope)
    return
  const list = [...tasks.value]
  const idx = list.findIndex(t => t.taskId === record.taskId)
  const scoped = { ...record, scope }
  if (idx >= 0)
    list.splice(idx, 1, { ...list[idx], ...scoped })
  else list.unshift(scoped)
  allTasks.value = [...list.slice(0, MAX_TASKS), ...(allTasks.value || []).filter(t => t.scope !== scope)].slice(0, 300)
}

export function removeTask(taskId: string) {
  allTasks.value = (allTasks.value || []).filter(t => t.scope !== activeScope.value || t.taskId !== taskId)
}
