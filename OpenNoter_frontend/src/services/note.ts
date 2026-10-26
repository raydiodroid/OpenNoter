import request from '@/lib/api'
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
    // F42：上传默认上限 500MB（UPLOAD_MAX_SIZE_MB），旧值 60s 在慢上行下
    // 必然超时（500MB / 60s 需要 ~8.3MB/s 上行）。改为 10 分钟后，
    // 与 nginx 的 client_max_body_size（512m）配合；真正的保护是服务端大小限制，
    // 不是前端超时。
    timeout: 600000,
  })

// 已删除 `imageProxy`（X1）：
//
// 它把任意 URL 拼成 `/api/image_proxy?url=...`。修 X1 之前，`VideoBanner` /
// `HistoryPanel` 无条件用它处理封面 —— 包括后端自己产的
// `http://localhost:8483/static/cover/...`。而 image_proxy 走 `assert_safe_url`，
// 先拦 loopback、再查图片 CDN 白名单（不含 localhost）⇒ **本地视频封面必 400**。
//
// 现在封面统一走 `lib/urls.ts` 的 `resolveCoverUrl()`：自有 `/static/` 直连，
// 只有第三方 CDN（B站/YouTube/抖音/快手，有防盗链才需要代理）才走后端代理。
// 这个 helper 已无调用点，保留它等于保留「把自有静态资源丢进代理」的反模式入口。
//
// 后端 `/api/image_proxy` 接口**仍然保留**（`resolveCoverUrl` 内部仍会用），
// 只是前端不再有这个直接拼 URL 的导出了。
