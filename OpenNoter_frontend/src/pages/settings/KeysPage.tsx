import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ActionButton,
  AlertDialog,
  Button,
  Cell,
  Column,
  Content,
  Dialog,
  DialogTrigger,
  Header,
  Heading,
  Picker,
  PickerItem,
  Row,
  SearchField,
  TableBody,
  TableHeader,
  TableView,
  Text,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CopyIcon from '@react-spectrum/s2/icons/Copy'
import DeleteIcon from '@react-spectrum/s2/icons/Delete'
import EditIcon from '@react-spectrum/s2/icons/Edit'
import HistoryIcon from '@react-spectrum/s2/icons/History'
import RefreshIcon from '@react-spectrum/s2/icons/Refresh'
import LockOpen from '@react-spectrum/s2/illustrations/linear/LockOpen'
import NoSearchResults from '@react-spectrum/s2/illustrations/linear/NoSearchResults'

import { EmptyState, LoadingState, Page, PageHeader, Panel, Section } from '@/components/Page'
import { StatusIndicator } from '@/components/StatusIndicator'
import {
  createKey,
  deleteKey,
  getKeyUsage,
  listKeys,
  rotateKey,
  updateKey,
  type IApiKey,
  type IApiKeyCreated,
  type IKeyUsage,
} from '@/services/auth'
import { formatDateTime, formatShortTime } from '@/lib/format'
import { toast } from '@/lib/toast'
import { Notice } from '@/components/Notice'

/* ── 样式 ─────────────────────────────────────────────────── */

const formGrid = style({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
  gap: 16,
  alignItems: 'end',
})

const formActions = style({
  display: 'flex',
  alignItems: 'end',
  gap: 8,
})

const keyBox = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  marginTop: 8,
  padding: 8,
  borderRadius: 'default',
  backgroundColor: 'layer-2',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
})

const keyCode = style({
  flexGrow: 1,
  minWidth: 0,
  font: 'code-sm',
  color: 'neutral',
  wordBreak: 'break-all',
})

const code = style({
  font: 'code-sm',
  color: 'neutral-subdued',
  whiteSpace: 'nowrap',
})

const nameCell = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  minWidth: 0,
})

const nameText = style({
  font: 'body',
  color: 'neutral',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

const descText = style({
  font: 'detail',
  color: 'neutral-subdued',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

const muted = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
})

const hint = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
  marginTop: 8,
})

const filterBar = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  flexWrap: 'wrap',
})

const searchBox = style({ width: 240 })

/** 表格铺满可用宽度（官方 TableView 示例的写法） */
const tableStyles = style({ width: 'full' })

/**
 * 行内操作按钮：右对齐，间距 8px —— S2 的 ActionButtonGroup 用的是 2/4/8px
 * 标度，表格行内这一组 28px 的图标按钮配 8px 间距才对得起（全站操作组
 * 也都是 8）。
 */
const rowActions = style({
  display: 'flex',
  justifyContent: 'end',
  gap: 8,
})

/** 状态筛选下拉框 */
const filterPicker = style({ width: 160 })

const expiringSoon = style({ font: 'detail', color: 'notice-700' })
const expiringUrgent = style({ font: 'detail', color: 'negative' })
const permanent = style({ font: 'detail', color: 'neutral-subdued' })

const summary = style({
  display: 'flex',
  gap: 16,
  flexWrap: 'wrap',
  font: 'detail',
  color: 'neutral-subdued',
})

const dialogBody = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 20,
  minWidth: 420,
  maxWidth: 640,
})

/* 用量弹窗里的统计块 */
const usageStats = style({
  display: 'flex',
  gap: 24,
  flexWrap: 'wrap',
})

const usageStat = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
})

const usageStatValue = style({
  font: 'heading-lg',
  color: 'neutral',
})

const usageStatLabel = style({
  font: 'detail',
  color: 'neutral-subdued',
})

const usageBlock = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
})

const usageBlockTitle = style({
  font: 'title',
  color: 'neutral',
  margin: 0,
})

const usageRow = style({
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  font: 'code-sm',
  color: 'neutral',
  paddingY: 2,
  borderBottomWidth: 1,
  borderBottomStyle: 'solid',
  borderBottomColor: 'gray-100',
})

const usagePath = style({
  flexGrow: 1,
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

const usageTime = style({ color: 'neutral-subdued', whiteSpace: 'nowrap' })
const usageMethod = style({ color: 'neutral-subdued', width: 44, flexShrink: 0 })
const usageStatus = style({ color: 'neutral-subdued', width: 32, flexShrink: 0, textAlign: 'end' })
const usageIp = style({ color: 'neutral-subdued', whiteSpace: 'nowrap' })
const usageCount = style({
  color: 'neutral',
  width: 48,
  flexShrink: 0,
  textAlign: 'end',
  fontVariantNumeric: 'tabular-nums',
})

/* ── 状态映射 ─────────────────────────────────────────────── */

type Badge = { label: string; variant: 'positive' | 'negative' | 'neutral' | 'notice' }

function statusBadge(key: IApiKey): Badge {
  if (key.status === 'revoked') {
    return key.in_rotation_grace
      ? { label: '即将吊销', variant: 'notice' }
      : { label: '已吊销', variant: 'negative' }
  }
  if (key.status === 'expired') return { label: '已过期', variant: 'neutral' }
  if (key.status === 'disabled') return { label: '停用', variant: 'neutral' }
  if (key.expires_in_days !== null && key.expires_in_days <= 7) {
    return { label: '即将到期', variant: 'notice' }
  }
  return { label: '有效', variant: 'positive' }
}

/** 到期文案：永久 / X 天后 / 已到期 */
function expiryText(key: IApiKey) {
  if (key.expires_in_days === null) return '永久'
  if (key.expires_in_days <= 0) return '已到期'
  return `${key.expires_in_days} 天后`
}

const EXPIRY_OPTIONS = [
  { id: 'never', label: '永不过期' },
  { id: '7', label: '7 天' },
  { id: '30', label: '30 天' },
  { id: '90', label: '90 天' },
  { id: '180', label: '180 天' },
  { id: '365', label: '365 天' },
]

const STATUS_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'active', label: '有效' },
  { id: 'expiring', label: '即将到期' },
  { id: 'expired', label: '已过期' },
  { id: 'revoked', label: '已吊销' },
  { id: 'unused', label: '从未使用' },
]

/* ── 一次性明文展示 ───────────────────────────────────────── */

function OneTimeSecret({ created, onDismiss }: { created: IApiKeyCreated; onDismiss: () => void }) {
  return (
    <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Notice variant="notice">
        <Text>仅此一次显示完整内容。关闭后无法再次查看，只能吊销重建。</Text>
      </Notice>
      <div className={keyBox}>
        <code className={keyCode}>{created.key}</code>
        <ActionButton
          aria-label="复制密钥"
          onPress={() => {
            navigator.clipboard.writeText(created.key)
            toast.success('已复制')
          }}
        >
          <CopyIcon />
        </ActionButton>
      </div>
      <div className={keyBox}>
        <code className={keyCode}>{created.usage_hint}</code>
        <ActionButton
          aria-label="复制环境变量命令"
          onPress={() => {
            navigator.clipboard.writeText(created.usage_hint)
            toast.success('已复制')
          }}
        >
          <CopyIcon />
        </ActionButton>
      </div>
      <div className={formActions}>
        <Button
          variant="accent"
          onPress={() => {
            onDismiss()
            toast.success('密钥已保存')
          }}
        >
          我已保存，关闭
        </Button>
      </div>
    </div>
  )
}

/* ── 编辑对话框 ───────────────────────────────────────────── */

function EditTrigger({ keyItem, onSaved }: { keyItem: IApiKey; onSaved: () => void }) {
  const [name, setName] = useState(keyItem.name)
  const [expiry, setExpiry] = useState(
    keyItem.expires_in_days === null ? 'never' : String(keyItem.expires_in_days)
  )
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      await updateKey(keyItem.id, {
        name: name.trim(),
        expires_days: expiry === 'never' ? 0 : Number(expiry),
      })
      toast.success('已保存')
      setOpen(false)
      onSaved()
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogTrigger isOpen={open} onOpenChange={setOpen}>
      <ActionButton aria-label="编辑密钥" isQuiet size="S">
        <EditIcon />
      </ActionButton>
      <Dialog isDismissible>
        <Header>
          <Heading>编辑密钥</Heading>
        </Header>
        <Content>
          <div className={dialogBody}>
            <TextField label="名称" value={name} onChange={setName} />
            <Picker
              label="有效期"
              selectedKey={expiry}
              onSelectionChange={k => setExpiry(String(k))}
            >
              {EXPIRY_OPTIONS.map(opt => (
                <PickerItem key={opt.id} id={opt.id}>
                  {opt.label}
                </PickerItem>
              ))}
            </Picker>
            <div className={formActions}>
              <Button variant="accent" onPress={save} isDisabled={saving || !name.trim()}>
                {saving ? '保存中…' : '保存'}
              </Button>
              <Button variant="secondary" onPress={() => setOpen(false)}>
                取消
              </Button>
            </div>
          </div>
        </Content>
      </Dialog>
    </DialogTrigger>
  )
}

/* ── 轮换对话框 ───────────────────────────────────────────── */

function RotateTrigger({ keyItem, onDone }: { keyItem: IApiKey; onDone: () => void }) {
  const [grace, setGrace] = useState(0)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [created, setCreated] = useState<IApiKeyCreated | null>(null)

  const run = async () => {
    setBusy(true)
    try {
      const result = await rotateKey(keyItem.id, grace)
      setCreated(result)
      setOpen(false)
      onDone()
    } catch {
      // 拦截器已提示
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <DialogTrigger isOpen={open} onOpenChange={setOpen}>
        <ActionButton aria-label="轮换密钥" isQuiet size="S">
          <RefreshIcon />
        </ActionButton>
        <Dialog isDismissible>
          <Header>
            <Heading>轮换密钥</Heading>
          </Header>
          <Content>
            <div className={dialogBody}>
              <Text>
                新建一把继承当前配置的密钥，旧的那把进入宽限期后自动吊销。名称、用途、限制都会保留，
                明文不会。
              </Text>
              <Picker
                label="旧密钥宽限期"
                selectedKey={String(grace)}
                onSelectionChange={k => setGrace(Number(k))}
              >
                <PickerItem id="0">立即吊销旧的</PickerItem>
                <PickerItem id="1">1 天</PickerItem>
                <PickerItem id="3">3 天</PickerItem>
                <PickerItem id="7">7 天</PickerItem>
                <PickerItem id="30">30 天</PickerItem>
              </Picker>
              <p className={muted}>怀疑密钥泄露时选「立即吊销旧的」，不要留宽限期。</p>
              <div className={formActions}>
                <Button variant="accent" onPress={run} isDisabled={busy}>
                  {busy ? '轮换中…' : '确认轮换'}
                </Button>
                <Button variant="secondary" onPress={() => setOpen(false)}>
                  取消
                </Button>
              </div>
            </div>
          </Content>
        </Dialog>
      </DialogTrigger>
      {created ? <OneTimeSecret created={created} onDismiss={() => setCreated(null)} /> : null}
    </>
  )
}

/* ── 删除确认 ─────────────────────────────────────────────── */

function DeleteTrigger({ keyItem, onDone }: { keyItem: IApiKey; onDone: () => void }) {
  const [busy, setBusy] = useState(false)

  const remove = async () => {
    setBusy(true)
    try {
      await deleteKey(keyItem.id, true)
      toast.success('已删除')
      onDone()
    } catch {
      // 拦截器已提示
    } finally {
      setBusy(false)
    }
  }

  return (
    <DialogTrigger>
      <ActionButton isQuiet size="S" aria-label={`删除 ${keyItem.name}`} isDisabled={busy}>
        <DeleteIcon />
      </ActionButton>
      <AlertDialog
        title="删除这把密钥？"
        variant="destructive"
        primaryActionLabel="删除"
        cancelLabel="取消"
        onPrimaryAction={remove}
      >
        删除后立即失效，且无法恢复。如果只是暂时不用，改有效期比删除更合适。
      </AlertDialog>
    </DialogTrigger>
  )
}

/* ── 用量详情对话框 ───────────────────────────────────────── */
function UsageTrigger({ keyItem }: { keyItem: IApiKey }) {
  const [usage, setUsage] = useState<IKeyUsage | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      setUsage(await getKeyUsage(keyItem.id, 30))
    } catch {
      // 拦截器已提示
    } finally {
      setLoading(false)
    }
  }

  return (
    <DialogTrigger
      isOpen={open}
      onOpenChange={o => {
        setOpen(o)
        if (o) void load()
      }}
    >
      <ActionButton aria-label="查看用量" isQuiet size="S">
        <HistoryIcon />
      </ActionButton>
      <Dialog isDismissible>
        <Header>
          <Heading>最近 30 天用量</Heading>
        </Header>
        <Content>
          {loading ? (
            <LoadingState label="统计中…" />
          ) : !usage ? (
            <Text>暂无数据</Text>
          ) : (
            <div className={dialogBody}>
              <div className={usageStats}>
                <div className={usageStat}>
                  <span className={usageStatValue}>{usage.total}</span>
                  <span className={usageStatLabel}>次调用</span>
                </div>
                <div className={usageStat}>
                  <span className={usageStatValue}>{usage.by_endpoint.length}</span>
                  <span className={usageStatLabel}>个端点</span>
                </div>
                <div className={usageStat}>
                  <span className={usageStatValue}>{usage.by_day.length}</span>
                  <span className={usageStatLabel}>天有活动</span>
                </div>
              </div>

              {usage.by_endpoint.length ? (
                <div className={usageBlock}>
                  <div className={usageBlockTitle}>按端点</div>
                  {usage.by_endpoint.map(e => (
                    <div key={e.path} className={usageRow}>
                      <code className={usagePath}>{e.path}</code>
                      <span className={usageCount}>{e.count}</span>
                    </div>
                  ))}
                </div>
              ) : null}

              {usage.recent.length ? (
                <div className={usageBlock}>
                  <div className={usageBlockTitle}>最近调用</div>
                  {usage.recent.slice(0, 20).map((r, i) => (
                    <div key={`${r.created_at}-${i}`} className={usageRow}>
                      <span className={usageTime}>{r.created_at?.slice(5, 16)}</span>
                      <span className={usageMethod}>{r.method}</span>
                      <code className={usagePath}>{r.path}</code>
                      <span className={usageStatus}>{r.status_code}</span>
                      <span className={usageIp}>{r.ip}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          )}
        </Content>
      </Dialog>
    </DialogTrigger>
  )
}

/* ── 页面 ─────────────────────────────────────────────────── */

export default function KeysPage() {
  const [keys, setKeys] = useState<IApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [expiry, setExpiry] = useState('never')
  const [newKey, setNewKey] = useState<IApiKeyCreated | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')

  const refresh = useCallback(async () => {
    try {
      setKeys(await listKeys())
    } catch {
      // 拦截器已提示
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const create = async () => {
    if (!name.trim()) {
      toast.error('请填写密钥名称')
      return
    }
    setCreating(true)
    try {
      const result = await createKey({
        name: name.trim(),
        expires_days: expiry === 'never' ? null : Number(expiry),
      })
      setNewKey(result)
      setName('')
      await refresh()
    } catch {
      // 拦截器已提示
    } finally {
      setCreating(false)
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return keys.filter(k => {
      if (q) {
        const hay = `${k.name} ${k.description ?? ''} ${k.prefix} ${k.key_id ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      switch (statusFilter) {
        case 'active':
          return k.status === 'active'
        case 'expiring':
          return k.status === 'active' && k.expires_in_days !== null && k.expires_in_days <= 7
        case 'expired':
          return k.status === 'expired'
        case 'revoked':
          return k.status === 'revoked'
        case 'unused':
          return !k.last_used_at
        default:
          return true
      }
    })
  }, [keys, query, statusFilter])

  const stats = useMemo(() => {
    const active = keys.filter(k => k.status === 'active').length
    const expiring = keys.filter(
      k => k.status === 'active' && k.expires_in_days !== null && k.expires_in_days <= 7
    ).length
    const unused = keys.filter(k => !k.last_used_at).length
    return { active, expiring, unused }
  }, [keys])

  return (
    <Page>
      <PageHeader
        title="密钥管理"
        description="给脚本或 agent 用的长期凭据。请求头里带 x-api-key 或 Authorization: Bearer。密钥等价于你的账号权限，用完可以随时吊销。"
      />

      <Section title="创建密钥">
        <Panel>
          <div className={formGrid}>
            <TextField
              label="名称"
              value={name}
              onChange={setName}
              placeholder="按使用者命名，如 obsidian-plugin"
            />
            <Picker
              label="有效期"
              selectedKey={expiry}
              onSelectionChange={k => setExpiry(String(k))}
            >
              {EXPIRY_OPTIONS.map(opt => (
                <PickerItem key={opt.id} id={opt.id}>
                  {opt.label}
                </PickerItem>
              ))}
            </Picker>
            <div className={formActions}>
              <Button variant="accent" onPress={create} isDisabled={creating}>
                {creating ? '创建中…' : '创建'}
              </Button>
            </div>
          </div>
          <p className={hint}>创建后完整内容只显示一次，请立即复制保存。</p>

          {newKey ? <OneTimeSecret created={newKey} onDismiss={() => setNewKey(null)} /> : null}
        </Panel>
      </Section>

      <Section title="已有密钥">
        <div className={filterBar}>
          <SearchField
            styles={searchBox}
            value={query}
            onChange={setQuery}
            placeholder="搜索名称或前缀"
          />
          <Picker
            label="状态"
            styles={filterPicker}
            selectedKey={statusFilter}
            onSelectionChange={k => setStatusFilter(String(k))}
          >
            {STATUS_FILTERS.map(f => (
              <PickerItem key={f.id} id={f.id}>
                {f.label}
              </PickerItem>
            ))}
          </Picker>
        </div>

        {/* 表格自带 1px gray-300 边框 + 6px 圆角，不再外套 Panel；
            加载态与空态用官方 loadingState / renderEmptyState 渲染在表格内部。
            空表时加 .table-empty —— S2 虚拟化表格无数据时表体高度为 0，
            内置空态会被定位到可视区之外（见 global.css）。 */}
        <div className={filtered.length === 0 ? 'table-empty' : undefined}>
          <TableView
            aria-label="API 密钥"
            selectionMode="none"
            density="spacious"
            overflowMode="truncate"
            loadingState={loading ? 'loading' : 'idle'}
            styles={tableStyles}
          >
            <TableHeader>
              {/* 列宽按实际内容测量值 + 32px 单元格内边距给定：
                前缀 code-sm 20 字符 ≈ 162px，最后使用时间戳 ≈ 118px，
                状态图标+文字 ≈ 80px，操作列 4 个 28px 图标按钮 + 8px 间距。
                「到期」与「最后使用」同宽：两列都是时间语义，对齐更好看。
                名称列不给宽度 —— 表格铺满容器时多出来的宽度由它吸收，
                否则空出来的地方会挂在最右侧的图标后面。 */}
              <Column isRowHeader>名称</Column>
              <Column width={200}>前缀</Column>
              <Column width={150}>最后使用</Column>
              <Column width={150}>到期</Column>
              <Column width={130}>状态</Column>
              <Column width={168}> </Column>
            </TableHeader>
            <TableBody
              items={filtered}
              renderEmptyState={() => (
                <EmptyState
                  illustration={keys.length ? <NoSearchResults /> : <LockOpen />}
                  title={keys.length ? '没有匹配的密钥' : '还没有密钥'}
                >
                  {keys.length
                    ? '换个筛选条件或清空搜索框。'
                    : '为脚本或 agent 创建一把，用完可以随时删除。'}
                </EmptyState>
              )}
            >
              {(key: IApiKey) => {
                const badge = statusBadge(key)
                return (
                  <Row key={key.id}>
                    <Cell>
                      <div className={nameCell}>
                        <span className={nameText} title={key.name}>
                          {key.name}
                        </span>
                        {key.description ? (
                          <span className={descText} title={key.description}>
                            {key.description}
                          </span>
                        ) : null}
                      </div>
                    </Cell>
                    <Cell>
                      <code className={code}>
                        {key.prefix}…{key.last_four ?? ''}
                      </code>
                    </Cell>
                    <Cell>
                      {key.last_used_at ? (
                        <span title={formatDateTime(key.last_used_at)}>
                          {formatShortTime(key.last_used_at)}
                        </span>
                      ) : (
                        <span className={muted}>从未使用</span>
                      )}
                    </Cell>
                    <Cell>
                      <span
                        className={
                          key.expires_in_days === null
                            ? permanent
                            : key.expires_in_days <= 7
                              ? expiringUrgent
                              : key.expires_in_days <= 30
                                ? expiringSoon
                                : permanent
                        }
                      >
                        {expiryText(key)}
                      </span>
                    </Cell>
                    <Cell>
                      <StatusIndicator tone={badge.variant}>{badge.label}</StatusIndicator>
                    </Cell>
                    <Cell>
                      {/* 操作直接铺在行内：编辑 / 轮换 / 用量 / 删除。
                        删除即吊销，记录一并从列表移除，不再留「已吊销」待恢复的行。 */}
                      <div className={rowActions}>
                        {key.status !== 'revoked' ? (
                          <>
                            <EditTrigger keyItem={key} onSaved={refresh} />
                            <RotateTrigger keyItem={key} onDone={refresh} />
                            <UsageTrigger keyItem={key} />
                          </>
                        ) : null}
                        <DeleteTrigger keyItem={key} onDone={refresh} />
                      </div>
                    </Cell>
                  </Row>
                )
              }}
            </TableBody>
          </TableView>
        </div>

        <div className={summary}>
          <span>{keys.length} 把密钥</span>
          <span>{stats.active} 把有效</span>
          {stats.expiring ? <span>{stats.expiring} 把 7 天内到期</span> : null}
          {stats.unused ? <span>{stats.unused} 把从未使用</span> : null}
        </div>
      </Section>
    </Page>
  )
}
