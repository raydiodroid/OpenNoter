import request from '@/lib/api'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatSource {
  text: string
  /** 后端 `_build_meta_chunk` / `_chunk_markdown` / `_chunk_transcript` 三种来源 */
  source_type: 'meta' | 'markdown' | 'transcript'
  section_title?: string
  start_time?: number
  end_time?: number
}

export interface AskResponse {
  answer: string
  sources: ChatSource[]
}

export type IndexStatus = 'idle' | 'indexing' | 'indexed' | 'failed'

export interface ChatStatusResponse {
  indexed: boolean
  status: IndexStatus
}

export const indexTask = async (taskId: string): Promise<void> => {
  // suppressToast：这个请求由 ChatPane 的轮询链调用，失败时调用方自己决定
  // 是否提示（网络抖动会静默重试 3 次），否则每次抖动都弹一个 toast。
  return await request.post('/chat/index', { task_id: taskId }, { suppressToast: true })
}

export const askQuestion = async (data: {
  task_id: string
  question: string
  history: ChatMessage[]
  provider_id: string
  model_name: string
}): Promise<AskResponse> => {
  // suppressToast：全局拦截器已经会弹一次后端返回的真实原因（如「模型供应商
  // 鉴权失败」），调用方再弹一句无信息量的「问答请求失败」会把前者盖掉。
  // 与 note.ts 的 generateNote/listTasks/deleteTask 保持同一范式。
  // F41：超时链必须严格递增 —— 后端预算 < 前端超时 < nginx 超时。
  //
  //   后端 CHAT_TOTAL_TIMEOUT = 90s   （chat_service.py，含工具多轮的总预算）
  //   前端（这里）            = 120s
  //   nginx /api/             = 180s  （nginx/default.conf 的 proxy_read_timeout）
  //
  // 旧值是 45s，比后端 90s 的预算还短 —— 工具多轮时后端还在正常跑，前端先断了，
  // 用户看到的是「问答失败」而不是真实结果（乃至后端仍会继续消耗 token）。
  // 任何一层设得 ≤ 下一层，都会变成「提前断点」。
  return await request.post('/chat/ask', data, { timeout: 120000, suppressToast: true })
}

export const getChatStatus = async (taskId: string): Promise<ChatStatusResponse> => {
  // taskId 可能来自 URL/历史记录，统一编码，避免特殊字符破坏查询串
  return await request.get(
    `/chat/status?task_id=${encodeURIComponent(taskId)}`,
    { suppressToast: true },
  )
}
