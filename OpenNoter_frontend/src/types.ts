export type TaskStatus =
  | 'PENDING'
  | 'PARSING'
  | 'DOWNLOADING'
  | 'TRANSCRIBING'
  | 'SUMMARIZING'
  | 'FORMATTING'
  | 'SAVING'
  | 'SUCCESS'
  | 'FAILED'

export interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker?: string
}

export interface Transcript {
  full_text: string
  language: string
  raw: unknown
  segments: TranscriptSegment[]
}

export interface AudioMeta {
  cover_url: string
  duration: number
  file_path: string
  platform: string
  raw_info: Record<string, any> | null
  title: string
  video_id: string
}

export interface MarkdownVersion {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at: string
}

export interface TaskFormData {
  video_url: string
  platform: string
  quality: string
  model_name: string
  provider_id: string
  style: string
  format: string[]
  extras?: string
  screenshot?: boolean
  link?: boolean
  video_understanding?: boolean
  video_interval?: number
  grid_size?: number[]
}

export interface Task {
  id: string
  status: TaskStatus
  markdown: MarkdownVersion[]
  transcript: Transcript
  audioMeta: AudioMeta
  createdAt: string
  formData: TaskFormData
  /**
   * 最近一次的状态消息（后端 `GET /tasks/{id}` 的 `message` 字段）。
   *
   * 失败时这里就是**真实原因**，例如
   * 「B站下载失败: ERROR: HTTP Error 412: Precondition Failed」。
   * 之前前端没保留这个字段，失败页只能显示一句泛泛的提示，
   * 用户完全不知道是 Cookie 过期还是链接失效。
   */
  message?: string
}

export interface Provider {
  id: string
  name: string
  logo: string
  apiKey: string
  baseUrl: string
  type: string
  enabled: number
}

export interface ModelItem {
  id: string | number
  provider_id: string
  model_name: string
  created_at?: string
  /**
   * 模型来源：
   * - `shared`：站点 providers 表里的模型（管理员配置，花站点额度）
   * - `own`   ：当前用户 BYOK 凭据里的模型（`provider_id` 形如 `own:llm`）
   *
   * 下拉里要显示「我的 / 站点」，否则用户不知道这次调用花的是谁的钱。
   */
  source?: 'shared' | 'own'
  /** 用户给凭据起的别名，仅 source=own 时有 */
  label?: string
}

/** /model_list/{pid} 返回的原始模型条目 */
export interface RemoteModel {
  id: string
  object?: string
  owned_by?: string
}

export interface NoteStatusResponse {
  status: TaskStatus
  result?: {
    markdown: string
    transcript: Transcript
    audio_meta: AudioMeta
  }
  message?: string
  task_id?: string
}
