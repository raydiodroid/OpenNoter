import request, { apiUrl } from '@/lib/api'
import type { NoteStatusResponse, TaskFormData } from '@/types'

/**
 * 提交生成任务。返回后端的 task_id。
 *
 * 走 POST /api/notes：provider_id / model_name 留空时后端自动挑已启用的那套，
 * platform 留空时按链接自动识别。
 *
 * suppressToast：失败提示由调用方自己弹（转写模型未就绪这类需要特殊引导），
 * 否则会跟全局提示重复弹两次。
 */
export const generateNote = (data: TaskFormData & { task_id?: string }) =>
  request.post<never, { task_id: string }>('/notes', data, { suppressToast: true })

export const getTaskStatus = (taskId: string) =>
  request.get<never, NoteStatusResponse>(`/tasks/${taskId}`, { suppressToast: true })

/** 任务列表项：后端只返回轻量字段，正文按需拉取 */
export interface TaskListItem {
  task_id: string
  status: string
  message: string | null
  has_result: boolean
  updated_at: string
  failed_at: string | null
}

export interface TaskListResponse {
  items: TaskListItem[]
  total: number
  limit: number
  offset: number
}

/**
 * 拉取当前账号的任务列表。
 *
 * 历史列表原先只存在浏览器 IndexedDB 里，换设备/清缓存就丢；
 * 改成从服务端拉，历史才真正跟着账号走。
 * suppressToast：轮询失败不该每 30 秒弹一次提示。
 */
export const listTasks = (params?: { limit?: number; offset?: number; status?: string }) =>
  request.get<never, TaskListResponse>('/tasks', { params, suppressToast: true })

/** 删除任务：连带清掉笔记结果、状态文件、向量索引和数据库记录。 */
export const deleteTask = (taskId: string) =>
  request.delete<never, unknown>(`/tasks/${taskId}`, { suppressToast: true })

export const uploadFile = (formData: FormData) =>
  request.post<never, { url: string }>('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })

/** 笔记图片走后端代理，避免跨域与 Referer 限制 */
export const imageProxy = (url: string) => apiUrl(`/image_proxy?url=${encodeURIComponent(url)}`)
