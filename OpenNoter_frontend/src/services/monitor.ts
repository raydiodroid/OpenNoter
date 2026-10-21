import request from '@/lib/api'

/**
 * 部署监控的数据契约。
 *
 * 后端 `/api/monitor/status` 的设计前提是「任何单项失败只降级该项」，因此
 * **不保证所有字段都存在**：探测超时会返回 `{ error }`、供应商没配会缺
 * `provider_id`、磁盘读不到会缺 `percent`。前端必须把「缺失」当一等公民，
 * 绝不能用 `?? 0` 把「没数据」读成「最健康」——旧实现正是这样把磁盘报错
 * 渲染成绿色「充足」的。
 *
 * 这里同时导出：
 *   - `IMonitorStatus`：校验通过后的强类型（组件内可以放心读）
 *   - `EMPTY_*`：兜底常量，用于「响应形状整体不可用」时仍然渲染出可读页面
 *   - `getMonitorStatus()`：发请求 + 运行时校验，形状不符则抛出可读错误
 */

/** 转写链路类型：决定这张卡该看什么，而不是永远看 groq 供应商。 */
export type AsrKind = 'local' | 'builtin' | 'provider' | 'unknown'

export interface MonitorLlm {
  name: string | null
  base_url: string | null
  /** 线上真正在用的供应商（来自 resolve_model_selection），不是「猜一个」 */
  provider_id: string | null
  model_name: string | null
  ok: boolean
  latency_ms: number | null
  error: string | null
  model_count: number
}

export interface MonitorAsr {
  kind: AsrKind
  transcriber_type: string | null
  ready: boolean
  error: string | null
  reason: string | null
  note: string | null
  model_size: string | null
  downloading: boolean | null
  configured_model: string | null
  // 仅 kind === 'provider' 时有意义
  name: string | null
  base_url: string | null
  ok: boolean
  latency_ms: number | null
  models: string[]
  model_count: number
  /** 配置的模型是否出现在端点返回的模型列表里；只有 kind==='provider' 才给 */
  configured_model_present?: boolean
}

export interface MonitorStorage {
  probe_path?: string
  total_gb?: number
  used_gb?: number
  free_gb?: number
  /** 可能是 null：盘符读不到时为 null，缺失时也按「未知」处理 */
  percent?: number | null
  /** 后端同时回传原始字节数：GB 字段是四舍五入后的展示值，字节才是精确值 */
  total_bytes?: number
  used_bytes?: number
  note_results_mb?: number
  /** 统计目录体积时被跳过的条目数（并发删除） */
  note_results_skipped?: number
  error?: string
  note_results_error?: string
}

export interface MonitorFfmpeg {
  ok: boolean
  version: string | null
  error: string | null
  path?: string | null
}

export interface MonitorTasks {
  /** 只统计可解析的状态文件 */
  total: number
  running: number
  /** 中间态停滞超过 stale_hours，疑似进程中断留下的僵尸 */
  stale: number
  success: number
  failed: number
  failed_24h: number
  /** 状态值无法识别 */
  unknown: number
  /** 文件无法解析（坏 JSON / 非对象） */
  unreadable: number
  zombie_scan: boolean
  note_dir: string
  stale_hours: number
  /** DB 里的任务总数（不随用户删除笔记而减少）。未跑迁移时为 undefined */
  db_total?: number
  /** DB 口径的 24h 失败数（含已被删除的任务） */
  db_failed_24h?: number
  /** DB 里已软删除（用户删了笔记）但仍计入累计的任务数 */
  db_deleted?: number
  /**
   * 累计任务：取「文件 ∪ DB」的较大值。
   * 只看文件会因用户删笔记而倒退，DB 这份兜住被删掉的那部分。
   */
  cumulative_total?: number
  /** 24h 失败（含已被删除的任务） */
  failed_24h_incl_deleted?: number
  /** 'files' = 只有文件口径；'files+db' = 已并入 DB 历史 */
  stats_source?: 'files' | 'files+db'
  error?: string
}

export interface MonitorMeta {
  generated_at: string
  note_dir: string
  probe_timeout_s: number
  request_budget_s: number
  ffmpeg_probe_timeout_s: number
  platform: string
}

/**
 * 各段落**可选**：后端承诺「任何一项失败只降级该项」，所以缺整段是合理状态
 * （例如该项被总预算超时丢弃）。渲染时必须把「缺整段」当成「数据缺失」，
 * 而不是当成「该项健康」——这正是旧实现 `data.llm.ok` 一崩全崩的根源。
 */
export interface IMonitorStatus {
  llm?: MonitorLlm
  asr?: MonitorAsr
  storage?: MonitorStorage
  ffmpeg?: MonitorFfmpeg
  tasks?: MonitorTasks
  meta?: MonitorMeta
}

/* ------------------------------------------------------------------ *
 * 兜底常量：整体形状不可用时，页面仍然能渲染出「全部未知」而不是白屏。
 * ------------------------------------------------------------------ */

export const EMPTY_LLM: MonitorLlm = {
  name: null,
  base_url: null,
  provider_id: null,
  model_name: null,
  ok: false,
  latency_ms: null,
  error: null,
  model_count: 0,
}

export const EMPTY_ASR: MonitorAsr = {
  kind: 'unknown',
  transcriber_type: null,
  ready: false,
  error: null,
  reason: null,
  note: null,
  model_size: null,
  downloading: null,
  configured_model: null,
  name: null,
  base_url: null,
  ok: false,
  latency_ms: null,
  models: [],
  model_count: 0,
}

export const EMPTY_STORAGE: MonitorStorage = {}

export const EMPTY_FFMPEG: MonitorFfmpeg = { ok: false, version: null, error: null, path: null }

export const EMPTY_TASKS: MonitorTasks = {
  total: 0,
  running: 0,
  stale: 0,
  success: 0,
  failed: 0,
  failed_24h: 0,
  unknown: 0,
  unreadable: 0,
  zombie_scan: false,
  note_dir: '',
  stale_hours: 0,
}

export const EMPTY_META: MonitorMeta = {
  generated_at: '',
  note_dir: '',
  probe_timeout_s: 0,
  request_budget_s: 0,
  ffmpeg_probe_timeout_s: 0,
  platform: '',
}

export const EMPTY_MONITOR_STATUS: IMonitorStatus = {
  llm: EMPTY_LLM,
  asr: EMPTY_ASR,
  storage: EMPTY_STORAGE,
  ffmpeg: EMPTY_FFMPEG,
  tasks: EMPTY_TASKS,
  meta: EMPTY_META,
}

/* ------------------------------------------------------------------ *
 * 运行时校验
 *
 * 分两层：
 *   1) 顶层必须是对象——`data: []` / `data: null` 这类形状说明请求打到了错误
 *      的地方（或后端挂了中间件），直接抛可读错误走 Notice，不渲染卡片；
 *   2) 段落缺失或不是对象——**不抛错**，由页面把该卡渲染成「数据缺失」。
 *      后端承诺单项失败只降级该项，所以缺整段是合法状态，不该让整页不可用。
 *
 * 段内字段一律宽松：字段级缺失由各卡渲染时降级成「未知」。
 * ------------------------------------------------------------------ */

class MonitorShapeError extends Error {
  constructor(detail: string) {
    super(`监控接口返回的数据形状不符合预期：${detail}`)
    this.name = 'MonitorShapeError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * 校验响应是否「至少像个监控响应」。
 *
 * 只判断顶层是对象；段落缺失由渲染层降级。真正的风险是顶层形状错乱
 * （例如后端返回 `data: []`），那会让组件里任何一次 `data.llm.ok` 都抛异常。
 */
export function isMonitorStatus(value: unknown): value is IMonitorStatus {
  return isRecord(value)
}

function assertMonitorStatus(value: unknown): IMonitorStatus {
  if (!isRecord(value)) {
    throw new MonitorShapeError(
      value === null
        ? 'data 为 null'
        : Array.isArray(value)
          ? `data 是数组（长度 ${value.length}）而不是对象`
          : `data 是 ${typeof value} 而不是对象`
    )
  }
  return value as unknown as IMonitorStatus
}

/**
 * 把「整段缺失」或「段落类型不对」统一收敛成 undefined。
 * 组件里因此只需要处理「有对象」和「没有」两种情况。
 */
export function sectionOf<T>(value: unknown, fallback: T): { data: T; present: boolean } {
  return isRecord(value)
    ? { data: value as unknown as T, present: true }
    : { data: fallback, present: false }
}

export class MonitorUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'MonitorUnavailableError'
  }
}

export const getMonitorStatus = async (signal?: AbortSignal): Promise<IMonitorStatus> => {
  const payload = await request.get<unknown>('/monitor/status', {
    suppressToast: true,
    signal,
  })
  try {
    return assertMonitorStatus(payload)
  } catch (err) {
    // 形状错乱不应冒泡成未捕获异常（旧实现直接白屏）。转成可读错误，
    // 由页面走 Notice 通道展示，其余卡片按「未知」降级。
    throw new MonitorUnavailableError(
      err instanceof MonitorShapeError ? err.message : '监控接口返回了无法识别的数据'
    )
  }
}

/** 供页面判断「拿到的数据是否可用」；与 isMonitorStatus 同义，语义更直白。 */
export const isUsableMonitorStatus = isMonitorStatus
