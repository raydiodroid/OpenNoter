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
