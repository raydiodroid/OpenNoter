import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button, Meter, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import RefreshIcon from '@react-spectrum/s2/icons/Refresh'
import ServerIcon from '@react-spectrum/s2/icons/CloudStateOnline'
import AudioIcon from '@react-spectrum/s2/icons/Transcript'
import QueueIcon from '@react-spectrum/s2/icons/ListMultiSelect'
import DiskIcon from '@react-spectrum/s2/icons/Data'
import FilmIcon from '@react-spectrum/s2/icons/Filmstrip'
import CloudStateError from '@react-spectrum/s2/illustrations/linear/CloudStateError'

import { EmptyState, Page, PageHeader, Panel, Section } from '@/components/Page'
import { StatusIndicator, type StatusTone } from '@/components/StatusIndicator'
import {
  EMPTY_ASR,
  EMPTY_FFMPEG,
  EMPTY_LLM,
  EMPTY_META,
  EMPTY_STORAGE,
  EMPTY_TASKS,
  getMonitorStatus,
  sectionOf,
  type IMonitorStatus,
  type MonitorAsr,
  type MonitorFfmpeg,
  type MonitorLlm,
  type MonitorMeta,
  type MonitorStorage,
  type MonitorTasks,
} from '@/services/monitor'
import { Notice } from '@/components/Notice'

const REFRESH_MS = 30000
/** 超过两个刷新周期没拿到新数据，就认为页面上的一切都「不再可信」。 */
const STALE_AFTER_MS = REFRESH_MS * 2

/** 缺失值的统一文案：任何读不到的数都渲染成它，绝不用 0 冒充。 */
const UNKNOWN = '未知'
/** 磁盘占用的三档阈值，徽标与 Meter 必须共用，否则会出现绿标配黄条。 */
const DISK_NOTICE_PCT = 80
const DISK_NEGATIVE_PCT = 90

const grid = style({
  display: 'grid',
  gridTemplateColumns: ['1fr', '1fr'],
  gap: 16,
})

const card = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const cardHead = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 8,
})
const cardTitle = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  font: 'title',
  color: 'neutral',
  margin: 0,
})
const row = style({
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: 12,
})
const rowLabel = style({ font: 'detail', color: 'neutral-subdued' })
const rowValue = style({
  font: 'ui',
  color: 'neutral',
  textAlign: 'end',
  fontVariantNumeric: 'tabular-nums',
})
const tags = style({ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 })
const muted = style({ font: 'detail-sm', color: 'neutral-subdued', margin: 0 })
const error = style({ font: 'detail-sm', color: 'negative', margin: 0 })
const warn = style({ font: 'detail-sm', color: 'notice-700', margin: 0 })
const updated = style({ font: 'detail-sm', color: 'neutral-subdued' })
const footer = style({ textAlign: 'center', marginTop: 24 })
const fullSpan = style({ gridColumn: '1 / -1' })
const valueWarn = style({ color: 'notice-700' })
const valueBad = style({ color: 'negative' })
const valueUnknown = style({ color: 'neutral-subdued' })

/* ── 折叠的「路径」细节：cwd 错位时能一眼看出来 ───────────────── */

const details = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  marginTop: 4,
})
const summary = style({
  cursor: 'pointer',
  color: 'neutral-subdued',
  font: 'detail-sm',
})
const pathText = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  margin: 0,
  paddingTop: 4,
  wordBreak: 'break-all',
})

/* ── 取值助手：区分「0」与「没有数据」 ────────────────────────── */

/** 只接受真实数字；null/undefined/NaN 一律返回 null（= 未知）。 */
function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatNumber(value: unknown, suffix = ''): string {
  const n = asNumber(value)
  return n === null ? UNKNOWN : `${n}${suffix}`
}

function formatLatency(value: unknown): { text: string; tone?: 'warn' } {
  const n = asNumber(value)
  if (n === null) return { text: UNKNOWN }
  return { text: `${n} ms`, tone: n > 2000 ? 'warn' : undefined }
}

/** percent 缺失或为 null 时必须返回 null——0 会被读成「最健康」。 */
function readPercent(storage: MonitorStorage): number | null {
  const p = asNumber(storage.percent)
  if (p === null) return null
  return Math.min(Math.max(p, 0), 100)
}

/**
 * 「已用 / 总量」：优先用后端回传的精确字节数自己换算。
 *
 * 后端 *_gb 字段是 round(...,1) 的 GiB 值，字段名却写 GB；直接拼在文案里会
 * 出现「500.0 + 400.0 ≠ 900.1」这种自相矛盾。有字节数就自己算，单位用 GiB。
 */
function diskUsedTotal(storage: MonitorStorage): string {
  const totalBytes = asNumber(storage.total_bytes)
  const usedBytes = asNumber(storage.used_bytes)
  if (totalBytes !== null && usedBytes !== null && totalBytes > 0) {
    const gib = (bytes: number) => (bytes / 1024 ** 3).toFixed(1)
    return `${gib(usedBytes)} / ${gib(totalBytes)} GiB`
  }
  const totalGb = asNumber(storage.total_gb)
  const usedGb = asNumber(storage.used_gb)
  if (totalGb === null || usedGb === null) return UNKNOWN
  return `${usedGb} / ${totalGb} GiB`
}

function diskTone(percent: number): 'positive' | 'notice' | 'negative' {
  if (percent >= DISK_NEGATIVE_PCT) return 'negative'
  if (percent >= DISK_NOTICE_PCT) return 'notice'
  return 'positive'
}

/**
 * 过期降级：数据超过两个刷新周期没更新时，宁可说「不知道」也不说「正常」。
 *
 * 只降 tone 不够——徽标文字还写着「正常」，过期的绿灯会被读成现状。所以
 * 健康态的文案也一并改成「已过期」，让「颜色 + 文字」都说同一件事。
 * 红色（故障）保持不变：故障不会因为数据过期而变好。
 */
function degrade(
  tone: StatusTone,
  label: string,
  stale: boolean
): { tone: StatusTone; label: string } {
  if (stale && tone === 'positive') return { tone: 'notice', label: `${label}（已过期）` }
  return { tone, label }
}

function healthy(label: string, stale: boolean): { tone: StatusTone; label: string } {
  return degrade('positive', label, stale)
}

function isApiKeyMissing(message: string | null | undefined): boolean {
  return typeof message === 'string' && message.includes('API Key')
}

/* ── 基础展示组件 ────────────────────────────────────────────── */

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: React.ReactNode
  tone?: 'warn' | 'bad' | 'unknown'
}) {
  const className =
    tone === 'bad'
      ? `${rowValue} ${valueBad}`
      : tone === 'warn'
        ? `${rowValue} ${valueWarn}`
        : tone === 'unknown'
          ? `${rowValue} ${valueUnknown}`
          : rowValue
  return (
    <div className={row}>
      <span className={rowLabel}>{label}</span>
      <span className={className}>{value}</span>
    </div>
  )
}

function MetricCard({
  icon,
  title,
  badge,
  children,
}: {
  icon: React.ReactNode
  title: string
  badge: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Panel>
      <div className={card}>
        <div className={cardHead}>
          <h2 className={cardTitle}>
            {icon}
            {title}
          </h2>
          {badge}
        </div>
        {children}
      </div>
    </Panel>
  )
}

/** 卡片标题右侧的状态徽标。tone 已在调用点算好（含过期降级）。 */
function Health({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return <StatusIndicator tone={tone}>{children}</StatusIndicator>
}

/** 可折叠的路径展示，用于核对「实际扫描/探测的是哪里」。 */
function PathDetail({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <details className={details}>
      <summary className={summary}>{label}</summary>
      <p className={pathText}>{value}</p>
    </details>
  )
}

/* ── 各卡状态推导 ───────────────────────────────────────────── */

function llmBadge(llm: MonitorLlm, stale: boolean): { tone: StatusTone; label: string } {
  if (isApiKeyMissing(llm.error)) return { tone: 'notice', label: '未配置密钥' }
  if (!llm.provider_id) return { tone: 'notice', label: '未配置供应商' }
  if (llm.ok) return healthy('正常', stale)
  return { tone: 'negative', label: '不可达' }
}

function asrBadge(asr: MonitorAsr, stale: boolean): { tone: StatusTone; label: string } {
  switch (asr.kind) {
    case 'local':
      return asr.ready ? healthy('就绪', stale) : { tone: 'negative', label: '模型未就绪' }
    case 'builtin':
      return healthy('在线引擎', stale)
    case 'provider':
      if (!asr.name && !asr.base_url) return { tone: 'negative', label: '未配置端点' }
      if (asr.ok) {
        if (asr.configured_model_present === false) {
          return { tone: 'notice', label: '配置的模型不在端点' }
        }
        return healthy('正常', stale)
      }
      return isApiKeyMissing(asr.error)
        ? { tone: 'notice', label: '未配置密钥' }
        : { tone: 'negative', label: '不可达' }
    default:
      return { tone: 'negative', label: '未知引擎' }
  }
}

function tasksBadge(tasks: MonitorTasks, stale: boolean): { tone: StatusTone; label: string } {
  if (tasks.error) return { tone: 'negative', label: '读取失败' }
  const failed24h = asNumber(tasks.failed_24h)
  const staleCount = asNumber(tasks.stale)
  const unreadable = asNumber(tasks.unreadable)
  if (failed24h === null || staleCount === null) {
    return { tone: 'notice', label: '数据不完整' }
  }
  if (staleCount > 0) return { tone: 'notice', label: `疑似中断 ${staleCount}` }
  if (failed24h > 0) return { tone: 'notice', label: `24h 失败 ${failed24h}` }
  if (unreadable !== null && unreadable > 0) {
    return { tone: 'notice', label: `${unreadable} 个文件无法解析` }
  }
  return healthy('无异常', stale)
}

function storageBadge(
  storage: MonitorStorage,
  stale: boolean
): { tone: StatusTone; label: string } {
  if (storage.error) return { tone: 'negative', label: '读取失败' }
  const percent = readPercent(storage)
  if (percent === null) return { tone: 'notice', label: '占用未知' }
  const tone = diskTone(percent)
  if (tone === 'positive') return healthy('空间充足', stale)
  const label = tone === 'negative' ? '接近写满' : '占用偏高'
  return { tone, label }
}

function ffmpegBadge(ff: MonitorFfmpeg, stale: boolean): { tone: StatusTone; label: string } {
  if (!ff.ok) return { tone: 'negative', label: '缺失' }
  // ok 但解析不出版本：不能给纯绿「可用」，否则和「未检测到」自相矛盾
  if (!ff.version) return { tone: 'notice', label: '版本未知' }
  return healthy('可用', stale)
}

/** 过期提示：页面顶部一条，避免旧绿灯被当成现状。 */
function staleMinutes(ageMs: number): number {
  return Math.max(1, Math.floor(ageMs / 60000))
}

export default function MonitorPage() {
  const [data, setData] = useState<IMonitorStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null)
  /** 只用于驱动「过期」相关的时间重算，不参与数据刷新节奏 */
  const [nowTick, setNowTick] = useState(() => Date.now())

  /** 请求序号：只接受最新一次请求的响应，避免慢响应覆盖新响应 */
  const seqRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  const fetchStatus = useCallback(async () => {
    const seq = ++seqRef.current
    // 取消上一次仍在途的请求：既省流量，也避免两条响应乱序写 state
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    try {
      const next = await getMonitorStatus(controller.signal)
      if (seq !== seqRef.current) return
      setData(next)
      setLastSuccessAt(Date.now())
      setErrorMessage(null)
    } catch (err: any) {
      if (controller.signal.aborted || seq !== seqRef.current) return
      // 失败时保留旧 data 供参照，但下面的 lastSuccessAt 会让所有徽标降级
      setErrorMessage(err?.message || err?.msg || '无法获取监控数据')
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const timer = setInterval(fetchStatus, REFRESH_MS)
    return () => clearInterval(timer)
  }, [fetchStatus])

  // 卸载时取消在途请求
  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  // 每 15s 重算一次「数据多旧了」，让过期提示无需等下一次请求
  useEffect(() => {
    const timer = setInterval(() => setNowTick(Date.now()), 15000)
    return () => clearInterval(timer)
  }, [])

  // 每段单独判断「有没有给」：缺整段说明该项没能采集上来（后端只降级该项），
  // 必须渲染成「数据缺失」而不是拿 EMPTY_* 冒充一个正常值。
  const llmSec = sectionOf<MonitorLlm>(data?.llm, EMPTY_LLM)
  const asrSec = sectionOf<MonitorAsr>(data?.asr, EMPTY_ASR)
  const storageSec = sectionOf<MonitorStorage>(data?.storage, EMPTY_STORAGE)
  const ffmpegSec = sectionOf<MonitorFfmpeg>(data?.ffmpeg, EMPTY_FFMPEG)
  const tasksSec = sectionOf<MonitorTasks>(data?.tasks, EMPTY_TASKS)
  const meta: MonitorMeta = sectionOf<MonitorMeta>(data?.meta, EMPTY_META).data

  const llm = llmSec.data
  const asr = asrSec.data
  const storage = storageSec.data
  const ffmpeg = ffmpegSec.data
  const tasks = tasksSec.data

  const ageMs = lastSuccessAt === null ? null : Math.max(0, nowTick - lastSuccessAt)
  const stale = ageMs !== null && ageMs > STALE_AFTER_MS
  // 从没成功过、又有错误 → 全部字段都不可信
  const neverLoaded = data === null

  /** 缺整段 = 该项没采集上来：一律 notice「数据缺失」，绝不能是绿色。 */
  const missing = { tone: 'notice' as StatusTone, label: '数据缺失' }

  const llmState = llmSec.present ? llmBadge(llm, stale || neverLoaded) : missing
  const asrState = asrSec.present ? asrBadge(asr, stale || neverLoaded) : missing
  const tasksState = tasksSec.present ? tasksBadge(tasks, stale || neverLoaded) : missing
  const storageState = storageSec.present ? storageBadge(storage, stale || neverLoaded) : missing
  const ffmpegState = ffmpegSec.present ? ffmpegBadge(ffmpeg, stale || neverLoaded) : missing

  const percent = readPercent(storage)
  const latency = formatLatency(llm.latency_ms)
  const asrLatency = formatLatency(asr.latency_ms)
  const asrModels = Array.isArray(asr.models) ? asr.models.filter(m => typeof m === 'string') : []
  const configureMismatch =
    asr.kind === 'provider' &&
    asr.configured_model !== null &&
    asr.configured_model_present === false

  // total 与各分类之和是否对得上：对不上说明有无法解析的文件，要显式展示
  const classifiedSum =
    (asNumber(tasks.running) ?? 0) +
    (asNumber(tasks.stale) ?? 0) +
    (asNumber(tasks.success) ?? 0) +
    (asNumber(tasks.failed) ?? 0) +
    (asNumber(tasks.unknown) ?? 0)
  const total = asNumber(tasks.total)
  const countsMismatch = total !== null && total !== classifiedSum

  // 页脚的超时数字来自后端 meta；meta 缺失时不要印出「0 秒」这种假精度
  const metaPresent = sectionOf<MonitorMeta>(data?.meta, EMPTY_META).present
  const footerParts = [`每 ${REFRESH_MS / 1000} 秒自动刷新`]
  if (metaPresent) {
    footerParts.push(
      `网关探测上限 ${formatNumber(meta.probe_timeout_s, ' 秒')}`,
      `FFmpeg ${formatNumber(meta.ffmpeg_probe_timeout_s, ' 秒')}`,
      `单次请求预算 ${formatNumber(meta.request_budget_s, ' 秒')}`
    )
  } else {
    footerParts.push('探测超时等运行参数暂不可用')
  }

  return (
    <Page>
      <PageHeader
        title="部署监控"
        description="网关与转写为实时探测，任务与磁盘为本地扫描；灰色或黄色表示数据缺失、过期或无法判断，需要人工确认。"
        actions={
          <>
            <Text styles={updated}>
              {lastSuccessAt
                ? `更新于 ${new Date(lastSuccessAt).toLocaleTimeString('zh-CN')}`
                : '—'}
            </Text>
            <Button variant="secondary" onPress={fetchStatus} isDisabled={loading}>
              <RefreshIcon />
              <Text>{loading ? '刷新中…' : '刷新'}</Text>
            </Button>
          </>
        }
      />

      {errorMessage ? (
        <Notice variant="negative">
          <Text>
            {errorMessage}
            {data ? '（下方为上一次成功获取的数据，仅供参考）' : ''}
          </Text>
        </Notice>
      ) : null}

      {stale && ageMs !== null ? (
        <Notice variant="notice">
          <Text>数据已过期 {staleMinutes(ageMs)} 分钟，下方健康状态无法代表当前情况。</Text>
        </Notice>
      ) : null}

      {neverLoaded && loading ? (
        <Section title="服务指标">
          <Panel>
            <EmptyState illustration={<CloudStateError />} title="正在读取监控数据">
              首次探测可能需要几秒钟。
            </EmptyState>
          </Panel>
        </Section>
      ) : data ? (
        <Section title="服务指标">
          <div className={grid}>
            {/* LLM 网关 */}
            <MetricCard
              icon={<ServerIcon />}
              title="LLM 网关"
              badge={<Health tone={llmState.tone}>{llmState.label}</Health>}
            >
              {!llmSec.present ? (
                <span className={warn}>本次未拿到 LLM 网关数据（该探测项可能超时或被降级）。</span>
              ) : null}
              <Metric
                label="供应商"
                value={
                  llm.provider_id
                    ? `${llm.provider_id}${llm.name ? ` · ${llm.name}` : ''}`
                    : '未配置'
                }
                tone={llm.provider_id ? undefined : 'unknown'}
              />
              <Metric
                label="模型"
                value={llm.model_name || '未配置'}
                tone={llm.model_name ? undefined : 'unknown'}
              />
              <Metric
                label="响应延迟"
                value={latency.text}
                tone={latency.tone ?? (asNumber(llm.latency_ms) === null ? 'unknown' : undefined)}
              />
              <Metric
                label="可用模型"
                value={
                  asNumber(llm.model_count) === null ? UNKNOWN : `${asNumber(llm.model_count)} 个`
                }
                tone={asNumber(llm.model_count) === null ? 'unknown' : undefined}
              />
              {llm.base_url ? <span className={muted}>{llm.base_url}</span> : null}
              {llm.error ? (
                <span className={isApiKeyMissing(llm.error) ? warn : error}>{llm.error}</span>
              ) : null}
            </MetricCard>

            {/* 转写链路：按 kind 分支，只有 provider 才谈得上「端点」 */}
            <MetricCard
              icon={<AudioIcon />}
              title="转写链路"
              badge={<Health tone={asrState.tone}>{asrState.label}</Health>}
            >
              {!asrSec.present ? (
                <span className={warn}>本次未拿到转写链路数据（该探测项可能超时或被降级）。</span>
              ) : null}
              <Metric
                label="引擎"
                value={asr.transcriber_type || UNKNOWN}
                tone={asr.transcriber_type ? undefined : 'unknown'}
              />

              {asr.kind === 'local' ? (
                <>
                  <Metric
                    label="模型大小"
                    value={asr.model_size || UNKNOWN}
                    tone={asr.model_size ? undefined : 'unknown'}
                  />
                  <Metric
                    label="模型状态"
                    value={asr.downloading ? '下载中' : asr.ready ? '已就绪' : '未就绪'}
                    tone={asr.ready ? undefined : 'bad'}
                  />
                  <span className={muted}>本地引擎，不经过网络端点，因此不显示响应延迟。</span>
                </>
              ) : null}

              {asr.kind === 'builtin' ? (
                <span className={muted}>
                  {asr.note || '在线引擎，端点与密钥由代码内置，不依赖供应商配置。'}
                </span>
              ) : null}

              {asr.kind === 'provider' ? (
                <>
                  <Metric
                    label="端点"
                    value={asr.name || asr.base_url || '未配置'}
                    tone={asr.name || asr.base_url ? undefined : 'bad'}
                  />
                  <Metric
                    label="响应延迟"
                    value={asrLatency.text}
                    tone={
                      asrLatency.tone ?? (asNumber(asr.latency_ms) === null ? 'unknown' : undefined)
                    }
                  />
                  <Metric
                    label="已配置模型"
                    value={asr.configured_model || '未配置'}
                    tone={asr.configured_model ? undefined : 'unknown'}
                  />
                  <Metric
                    label="语音模型"
                    value={
                      asNumber(asr.model_count) === null
                        ? UNKNOWN
                        : `${asNumber(asr.model_count)} 个`
                    }
                    tone={
                      configureMismatch
                        ? 'warn'
                        : asNumber(asr.model_count) === null
                          ? 'unknown'
                          : undefined
                    }
                  />
                  {configureMismatch ? (
                    <span className={warn}>
                      配置的模型 {asr.configured_model} 不在端点返回的模型列表中，任务可能失败。
                    </span>
                  ) : null}
                  {asrModels.length > 0 ? (
                    <div className={tags}>
                      {asrModels.map(model => (
                        <Badge
                          key={model}
                          variant="gray"
                          fillStyle="subtle"
                          UNSAFE_className="u-mono"
                        >
                          {model}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                  {asr.base_url ? <span className={muted}>{asr.base_url}</span> : null}
                </>
              ) : null}

              {asr.kind === 'unknown' ? (
                <span className={error}>
                  {asr.error || '无法识别当前转写引擎，请在「音频转写配置」中确认。'}
                </span>
              ) : null}

              {asr.reason ? <span className={warn}>{asr.reason}</span> : null}
              {asr.error && asr.kind !== 'unknown' ? (
                <span className={error}>{asr.error}</span>
              ) : null}
            </MetricCard>

            {/* 任务队列 */}
            <MetricCard
              icon={<QueueIcon />}
              title="任务队列"
              badge={<Health tone={tasksState.tone}>{tasksState.label}</Health>}
            >
              {!tasksSec.present ? (
                <span className={warn}>本次未拿到任务统计（该项可能超时或被降级）。</span>
              ) : tasks.error ? (
                <span className={error}>{tasks.error}</span>
              ) : (
                <>
                  <Metric
                    label="进行中"
                    value={formatNumber(tasks.running)}
                    tone={asNumber(tasks.running) === null ? 'unknown' : undefined}
                  />
                  <Metric
                    label="疑似中断"
                    value={formatNumber(tasks.stale)}
                    tone={
                      asNumber(tasks.stale) === null
                        ? 'unknown'
                        : (asNumber(tasks.stale) ?? 0) > 0
                          ? 'warn'
                          : undefined
                    }
                  />
                  <Metric
                    label="成功"
                    value={formatNumber(tasks.success)}
                    tone={asNumber(tasks.success) === null ? 'unknown' : undefined}
                  />
                  <Metric
                    label="失败"
                    value={formatNumber(tasks.failed)}
                    tone={
                      asNumber(tasks.failed) === null
                        ? 'unknown'
                        : (asNumber(tasks.failed) ?? 0) > 0
                          ? 'bad'
                          : undefined
                    }
                  />
                  {countsMismatch || (asNumber(tasks.unreadable) ?? 0) > 0 ? (
                    <Metric label="无法解析" value={formatNumber(tasks.unreadable)} tone="warn" />
                  ) : null}
                  {(asNumber(tasks.unknown) ?? 0) > 0 ? (
                    <Metric label="状态未知" value={formatNumber(tasks.unknown)} tone="warn" />
                  ) : null}
                  <Metric
                    label="可解析任务"
                    value={formatNumber(tasks.total)}
                    tone={asNumber(tasks.total) === null ? 'unknown' : undefined}
                  />
                  {/* 累计口径 = max(文件, DB)。用户删笔记会删掉 .status.json，
                      只看文件会让这个数字倒退（旧实现的缺陷）。 */}
                  <Metric
                    label="累计任务"
                    value={formatNumber(tasks.cumulative_total ?? tasks.total)}
                    tone={
                      asNumber(tasks.cumulative_total ?? tasks.total) === null ? 'unknown' : undefined
                    }
                  />
                  {(asNumber(tasks.db_deleted) ?? 0) > 0 ? (
                    <Metric
                      label="已删但仍计累计"
                      value={formatNumber(tasks.db_deleted)}
                      tone="unknown"
                    />
                  ) : null}
                  {asNumber(tasks.failed_24h_incl_deleted) !== null ? (
                    <Metric
                      label="24h 失败（含已删）"
                      value={formatNumber(tasks.failed_24h_incl_deleted)}
                      tone={
                        (asNumber(tasks.failed_24h_incl_deleted) ?? 0) > 0 ? 'bad' : undefined
                      }
                    />
                  ) : null}
                  {countsMismatch ? (
                    <span className={warn}>
                      分类合计 {classifiedSum} 与可解析任务 {total} 不一致，存在无法归类的状态文件。
                    </span>
                  ) : null}
                  {tasks.stats_source === 'files' ? (
                    <span className={muted}>
                      仅文件口径：删除笔记会让统计倒退。执行 python migrate_task_status.py
                      可并入数据库历史。
                    </span>
                  ) : tasks.stats_source === 'files+db' ? (
                    <span className={muted}>文件 + 数据库双口径；数据库部分不随删除笔记减少。</span>
                  ) : null}
                  {tasks.zombie_scan === false ? (
                    <span className={muted}>本次未执行疑似中断扫描。</span>
                  ) : (
                    <span className={muted}>
                      中间态停滞超过 {formatNumber(tasks.stale_hours, ' 小时')} 计为「疑似中断」。
                    </span>
                  )}
                  <PathDetail label="任务目录" value={tasks.note_dir} />
                </>
              )}
            </MetricCard>

            {/* 磁盘空间 */}
            <MetricCard
              icon={<DiskIcon />}
              title="磁盘空间"
              badge={<Health tone={storageState.tone}>{storageState.label}</Health>}
            >
              {!storageSec.present ? (
                <span className={warn}>本次未拿到磁盘数据（该项可能超时或被降级）。</span>
              ) : storage.error ? (
                <span className={error}>{storage.error}</span>
              ) : percent === null ? (
                <span className={warn}>无法获取磁盘占用比例，请展开下方探测路径核对。</span>
              ) : (
                <>
                  <Meter
                    label="占用比例"
                    value={percent}
                    maxValue={100}
                    variant={diskTone(percent)}
                    formatOptions={{ style: 'percent', maximumFractionDigits: 0 }}
                  />
                  <Metric
                    label="已用 / 总量"
                    value={diskUsedTotal(storage)}
                    tone={
                      asNumber(storage.used_gb) === null || asNumber(storage.total_gb) === null
                        ? 'unknown'
                        : undefined
                    }
                  />
                </>
              )}
              <Metric
                label="任务产物占用"
                value={formatNumber(storage.note_results_mb, ' MB')}
                tone={asNumber(storage.note_results_mb) === null ? 'unknown' : undefined}
              />
              {(asNumber(storage.note_results_skipped) ?? 0) > 0 ? (
                <span className={muted}>
                  统计时有 {storage.note_results_skipped} 个文件被并发删除，已跳过。
                </span>
              ) : null}
              {storage.note_results_error ? (
                <span className={warn}>任务产物统计失败：{storage.note_results_error}</span>
              ) : null}
              <PathDetail label="探测路径" value={storage.probe_path} />
            </MetricCard>

            {/* FFmpeg */}
            <div className={fullSpan}>
              <MetricCard
                icon={<FilmIcon />}
                title="FFmpeg"
                badge={<Health tone={ffmpegState.tone}>{ffmpegState.label}</Health>}
              >
                {!ffmpegSec.present ? (
                  <span className={warn}>本次未拿到 FFmpeg 探测结果（该项可能超时或被降级）。</span>
                ) : null}
                <Metric
                  label="版本"
                  value={ffmpeg.version || UNKNOWN}
                  tone={ffmpeg.version ? undefined : 'unknown'}
                />
                <span className={muted}>
                  视频下载与音频抽取依赖它；封面截图失败不会让任务失败，但笔记里会缺图。
                </span>
                {ffmpeg.error ? (
                  <span className={ffmpeg.ok ? warn : error}>{ffmpeg.error}</span>
                ) : null}
                <PathDetail label="可执行文件" value={ffmpeg.path} />
              </MetricCard>
            </div>
          </div>
        </Section>
      ) : null}

      <div className={footer}>
        <Text styles={muted}>{footerParts.join(' · ')}</Text>
        {meta.platform ? <Text styles={muted}> · {meta.platform}</Text> : null}
      </div>
    </Page>
  )
}
