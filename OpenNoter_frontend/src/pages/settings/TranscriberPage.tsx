import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActionButton,
  AlertDialog,
  Button,
  Cell,
  Column,
  Content,
  DialogTrigger,
  Heading,
  IllustratedMessage,
  Picker,
  PickerItem,
  Row,
  TableBody,
  TableHeader,
  TableView,
  Text,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import AddIcon from '@react-spectrum/s2/icons/Add'
import AlertIcon from '@react-spectrum/s2/icons/AlertTriangle'
import DeleteIcon from '@react-spectrum/s2/icons/Delete'
import DownloadIcon from '@react-spectrum/s2/icons/Download'

import { Page, PageHeader, Panel, Section } from '@/components/Page'
import { StatusIndicator } from '@/components/StatusIndicator'
import {
  addWhisperModel,
  deleteWhisperModel,
  downloadModel,
  getModelsStatus,
  getTranscriberConfig,
  updateAsrEndpoint,
  updateTranscriberConfig,
  type ModelStatus,
  type TranscriberConfig,
} from '@/services/transcriber'
import { toast } from '@/lib/toast'
import { Notice } from '@/components/Notice'
import { Fold } from '@/components/Fold'

const isWhisperType = (type: string) => type === 'fast-whisper' || type === 'mlx-whisper'

// 无 gap：间距由各子项自带，Fold 收起时随之消失（见 Fold.tsx 注释）
const pageStack = style({ display: 'flex', flexDirection: 'column' })
const maxForm = style({ maxWidth: 760 })
// 无 gap：竖向间距由各子项自己带（Fold 的 gap / 固定块的 marginTop），
// 这样 Fold 收起时间距随之消失，不留空白。
const formStack = style({ display: 'flex', flexDirection: 'column', maxWidth: 560 })
const spacedAfterFirst = style({ marginTop: 16 })
const actions = style({ display: 'flex', justifyContent: 'end', gap: 8 })
const cellText = style({ font: 'ui', color: 'neutral' })

/** 表格铺满可用宽度（官方 TableView 示例的写法） */
const tableStyles = style({ width: 'full' })
const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const inlineRow = style({ display: 'flex', alignItems: 'end', gap: 12, flexWrap: 'wrap' })
const grow = style({ flexGrow: 1, minWidth: 220 })
const nameField = style({ width: 200 })
const addForm = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
  paddingTop: 24,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 1,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const SIZE_HINT: Record<string, string> = {
  tiny: '~75MB',
  base: '~150MB',
  small: '~500MB',
  medium: '~1.5GB',
  'large-v3': '~3GB',
  'large-v3-turbo': '~1.6GB',
}

export default function TranscriberPage({ embedded = false }: { embedded?: boolean }) {
  const [config, setConfig] = useState<TranscriberConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const [selectedType, setSelectedType] = useState('')
  const [selectedSize, setSelectedSize] = useState('')
  const [whisperStatus, setWhisperStatus] = useState<ModelStatus[]>([])
  const [mlxStatus, setMlxStatus] = useState<ModelStatus[]>([])
  const [mlxAvailable, setMlxAvailable] = useState(false)

  const [asrBaseUrl, setAsrBaseUrl] = useState('')
  const [asrApiKey, setAsrApiKey] = useState('')
  const [asrModel, setAsrModel] = useState('')

  const [newName, setNewName] = useState('')
  const [newTarget, setNewTarget] = useState('')
  const [adding, setAdding] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const previousFailures = useRef<Set<string> | null>(null)

  const refreshStatuses = useCallback(async () => {
    try {
      const data = await getModelsStatus()
      setWhisperStatus(data.whisper)
      setMlxStatus(data.mlx_whisper)
      setMlxAvailable(data.mlx_available)

      // 只对「本次新出现的失败」提示一次，避免轮询反复弹
      const failedNow = new Map<string, ModelStatus>()
      data.whisper.forEach(model => model.failed && failedNow.set(model.model_size, model))
      data.mlx_whisper.forEach(
        model => model.failed && failedNow.set(`mlx-${model.model_size}`, model)
      )

      if (previousFailures.current === null) {
        previousFailures.current = new Set(failedNow.keys())
      } else {
        failedNow.forEach((model, key) => {
          if (!previousFailures.current!.has(key)) {
            const detail = model.error ? `：${model.error.slice(0, 100)}` : ''
            toast.error(`模型 ${model.model_size} 下载失败${detail}`)
          }
        })
        previousFailures.current = new Set(failedNow.keys())
      }
    } catch {
      // 静默
    }
  }, [])

  const reloadConfig = useCallback(async () => {
    try {
      setConfig(await getTranscriberConfig())
    } catch {
      // 静默
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const data = await getTranscriberConfig()
        setConfig(data)
        setSelectedType(data.transcriber_type)
        setSelectedSize(data.whisper_model_size)
      } catch {
        toast.error('获取转写器配置失败')
      } finally {
        setLoading(false)
      }
    }
    load()
    refreshStatuses()
  }, [refreshStatuses])

  useEffect(() => {
    if (!config) return
    setAsrBaseUrl(previous => previous || config.resolved_endpoint || '')
    setAsrModel(previous => previous || config.resolved_model || '')
  }, [config])

  useEffect(() => {
    const downloading =
      whisperStatus.some(model => model.downloading) || mlxStatus.some(model => model.downloading)
    if (!downloading) return
    const timer = setInterval(refreshStatuses, 3000)
    return () => clearInterval(timer)
  }, [whisperStatus, mlxStatus, refreshStatuses])

  const currentModels = selectedType === 'mlx-whisper' ? mlxStatus : whisperStatus

  /** 选中的 Whisper 模型还没下载时，保存前要先确认（用 S2 AlertDialog，不用浏览器原生 confirm） */
  const needsModelDownload = () => {
    if (!isWhisperType(selectedType)) return false
    const target = currentModels.find(model => model.model_size === selectedSize)
    return Boolean(target && !target.downloaded && !target.downloading)
  }

  const save = async () => {
    setSaving(true)
    try {
      if (selectedType === 'groq') {
        if (!asrBaseUrl.trim()) {
          toast.error('请填写端点地址')
          return
        }
        await updateAsrEndpoint({
          base_url: asrBaseUrl.trim(),
          api_key: asrApiKey.trim() || undefined,
          model: asrModel.trim() || undefined,
        })
        setAsrApiKey('')
      }

      await updateTranscriberConfig({
        transcriber_type: selectedType,
        whisper_model_size: isWhisperType(selectedType) ? selectedSize : undefined,
      })
      toast.success('转写器配置已保存')
      await reloadConfig()
      await refreshStatuses()
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const download = async (modelSize: string) => {
    try {
      await downloadModel({ model_size: modelSize, transcriber_type: selectedType })
      toast.success(`模型 ${modelSize} 开始下载`)
      setTimeout(refreshStatuses, 1000)
    } catch {
      toast.error('下载请求失败')
    }
  }

  const addCustom = async () => {
    const name = newName.trim()
    const target = newTarget.trim()
    if (!name || !target) {
      toast.error('请填写模型名称和 HF repo_id / 本地路径')
      return
    }
    setAdding(true)
    try {
      await addWhisperModel({ name, target })
      toast.success(`已添加自定义模型 ${name}`)
      setNewName('')
      setNewTarget('')
      await reloadConfig()
      await refreshStatuses()
    } catch {
      // 拦截器已提示
    } finally {
      setAdding(false)
    }
  }

  const removeCustom = async (name: string) => {
    try {
      await deleteWhisperModel(name)
      toast.success(`已删除自定义模型 ${name}`)
      if (selectedSize === name) setSelectedSize('tiny')
      await reloadConfig()
      await refreshStatuses()
    } catch {
      // 拦截器已提示
    }
  }

  if (loading) {
    const loadingPanel = (
      <Panel>
        <Text styles={muted}>加载中…</Text>
      </Panel>
    )
    if (embedded) return loadingPanel
    return (
      <Page>
        <PageHeader title="音频转写" description="加载中…" />
        {loadingPanel}
      </Page>
    )
  }

  if (!config) {
    const errorPanel = (
      <Panel>
        <IllustratedMessage>
          <AlertIcon />
          <Heading>无法加载转写配置</Heading>
          <Content>请确认后端已启动，然后刷新页面。</Content>
        </IllustratedMessage>
      </Panel>
    )
    if (embedded) return errorPanel
    return (
      <Page>
        <PageHeader title="音频转写" />
        {errorPanel}
      </Page>
    )
  }

  const statusOf = (model: ModelStatus) =>
    model.downloaded ? (
      <StatusIndicator tone="positive">已下载</StatusIndicator>
    ) : model.downloading ? (
      <StatusIndicator tone="notice">下载中</StatusIndicator>
    ) : model.failed ? (
      <StatusIndicator tone="negative">下载失败</StatusIndicator>
    ) : (
      <StatusIndicator tone="neutral">未下载</StatusIndicator>
    )

  const content = (
    <div className={pageStack}>
      <Section
        title="转写引擎"
        description="保存后对新任务立即生效。在线引擎无需下载模型，本地 Whisper 首次使用需要下载。"
      >
        <div className={maxForm}>
          <Panel>
            <div className={formStack}>
              <Picker
                label="转写器类型"
                selectedKey={selectedType}
                onSelectionChange={key => setSelectedType(String(key))}
              >
                {config.available_types.map(type => (
                  <PickerItem key={type.value} id={type.value} textValue={type.label}>
                    {type.label}
                  </PickerItem>
                ))}
              </Picker>

              <Fold open={selectedType === 'groq'} gap={16}>
                <>
                  <Notice variant="informative">
                    <Text>
                      任意 OpenAI 兼容的语音转写服务（调用 /audio/transcriptions），可填 Groq
                      云端，也可填自建网关。
                    </Text>
                  </Notice>
                  <TextField
                    label="端点地址"
                    value={asrBaseUrl}
                    onChange={setAsrBaseUrl}
                    placeholder="https://api.groq.com/openai/v1"
                  />
                  <TextField
                    label="API Key"
                    type="password"
                    value={asrApiKey}
                    onChange={setAsrApiKey}
                    placeholder={config.has_asr_key ? '已配置，留空则不修改' : '尚未配置，请填写'}
                  />
                  <TextField
                    label="模型名"
                    value={asrModel}
                    onChange={setAsrModel}
                    placeholder="whisper-large-v3 / sensevoice"
                  />
                </>
              </Fold>

              <Fold open={isWhisperType(selectedType)} gap={16}>
                <>
                  <Picker
                    label="Whisper 模型大小"
                    selectedKey={selectedSize}
                    onSelectionChange={key => setSelectedSize(String(key))}
                  >
                    {config.whisper_model_sizes.map(size => {
                      const status = currentModels.find(model => model.model_size === size)
                      return (
                        <PickerItem key={size} id={size} textValue={size}>
                          {status?.downloaded ? `${size}（已下载）` : size}
                        </PickerItem>
                      )
                    })}
                  </Picker>
                  <Text styles={muted}>模型越大精度越高，但速度更慢、占用更多显存。</Text>
                </>
              </Fold>

              {selectedType === 'mlx-whisper' && !config.mlx_whisper_available ? (
                <div className={spacedAfterFirst}>
                  <Notice variant="notice">
                    <AlertIcon />
                    <Text>
                      MLX Whisper 当前不可用：需要 macOS 并安装 mlx-whisper，安装后重启后端生效。
                    </Text>
                  </Notice>
                </div>
              ) : null}

              <div className={`${actions} ${spacedAfterFirst}`}>
                <DialogTrigger
                  isOpen={confirmOpen}
                  onOpenChange={open => {
                    if (!open) setConfirmOpen(false)
                  }}
                >
                  <Button
                    variant="accent"
                    onPress={() => {
                      if (needsModelDownload()) {
                        setConfirmOpen(true)
                        return
                      }
                      void save()
                    }}
                    isDisabled={saving || (selectedType === 'mlx-whisper' && !mlxAvailable)}
                  >
                    {saving ? '保存中…' : '保存配置'}
                  </Button>
                  <AlertDialog
                    title="首次转写需要下载模型"
                    variant="warning"
                    primaryActionLabel="继续保存"
                    cancelLabel="取消"
                    onPrimaryAction={() => {
                      setConfirmOpen(false)
                      void save()
                    }}
                  >
                    首次转写时会下载 {selectedSize} 模型（{SIZE_HINT[selectedSize] || '体积未知'}
                    ）。 网络较差时容易中断，推荐优先使用在线引擎。
                  </AlertDialog>
                </DialogTrigger>
              </div>
            </div>
          </Panel>
        </div>
      </Section>

      <Fold open={isWhisperType(selectedType)} gap={32}>
        <Section
          title={selectedType === 'mlx-whisper' ? 'MLX Whisper 模型' : 'Faster Whisper 模型'}
        >
          <TableView
            aria-label="转写模型"
            selectionMode="none"
            density="spacious"
            overflowMode="truncate"
            styles={tableStyles}
          >
            <TableHeader>
              <Column isRowHeader>模型</Column>
              <Column width={120}>状态</Column>
              <Column width={240}>体积 / 错误</Column>
              <Column width={104}>操作</Column>
            </TableHeader>
            <TableBody items={currentModels}>
              {(model: ModelStatus) => (
                <Row>
                  <Cell>
                    <Text styles={cellText}>{model.model_size}</Text>
                  </Cell>
                  <Cell>{statusOf(model)}</Cell>
                  <Cell>
                    <span className={muted} title={model.error}>
                      {model.failed && model.error
                        ? model.error.slice(0, 160)
                        : SIZE_HINT[model.model_size] || '体积未知'}
                    </span>
                  </Cell>
                  <Cell>
                    {!model.downloaded && !model.downloading ? (
                      <Button
                        variant="secondary"
                        size="S"
                        onPress={() => download(model.model_size)}
                      >
                        <DownloadIcon />
                        <Text>{model.failed ? '重试' : '下载'}</Text>
                      </Button>
                    ) : null}
                  </Cell>
                </Row>
              )}
            </TableBody>
          </TableView>
        </Section>
      </Fold>

      <Fold open={selectedType === 'fast-whisper'} gap={32}>
        <Section title="自定义模型">
          <div className={maxForm}>
            <Panel>
              <div className={formStack}>
                <Text styles={muted}>
                  填 HF repo_id（如
                  Systran/faster-whisper-large-v3，会自动下载）或本地模型目录（目录内需含
                  model.bin，下载会跳过）。添加后即可在上方「模型大小」里选用。
                </Text>

                {config.whisper_custom_models &&
                Object.keys(config.whisper_custom_models).length > 0 ? (
                  <TableView
                    aria-label="自定义模型"
                    selectionMode="none"
                    density="spacious"
                    overflowMode="truncate"
                    styles={tableStyles}
                  >
                    <TableHeader>
                      <Column isRowHeader>名称</Column>
                      <Column>仓库或路径</Column>
                      <Column width={120}>状态</Column>
                      <Column width={64}>操作</Column>
                    </TableHeader>
                    <TableBody items={Object.entries(config.whisper_custom_models)}>
                      {([name, target]: [string, string]) => {
                        const status = whisperStatus.find(model => model.model_size === name)
                        return (
                          <Row key={name}>
                            <Cell>
                              <Text styles={cellText}>{name}</Text>
                            </Cell>
                            <Cell>
                              <span className={muted} title={target}>
                                {target}
                              </span>
                            </Cell>
                            <Cell>
                              {status?.downloaded ? (
                                <StatusIndicator tone="positive">已下载</StatusIndicator>
                              ) : null}
                              {status?.downloading ? (
                                <StatusIndicator tone="notice">下载中</StatusIndicator>
                              ) : null}
                              {status?.failed ? (
                                <StatusIndicator tone="negative">下载失败</StatusIndicator>
                              ) : null}
                            </Cell>
                            <Cell>
                              <ActionButton
                                isQuiet
                                size="S"
                                aria-label={`删除 ${name}`}
                                onPress={() => removeCustom(name)}
                              >
                                <DeleteIcon />
                              </ActionButton>
                            </Cell>
                          </Row>
                        )
                      }}
                    </TableBody>
                  </TableView>
                ) : (
                  <Text styles={muted}>还没有自定义模型。</Text>
                )}

                <div className={addForm}>
                  <div className={inlineRow}>
                    <TextField
                      label="模型名称"
                      value={newName}
                      onChange={setNewName}
                      placeholder="my-large-v3"
                      styles={nameField}
                    />
                    <TextField
                      label="HF repo_id 或本地路径"
                      value={newTarget}
                      onChange={setNewTarget}
                      placeholder="Systran/faster-whisper-large-v3"
                      styles={grow}
                    />
                    <Button variant="secondary" onPress={addCustom} isDisabled={adding}>
                      <AddIcon />
                      <Text>{adding ? '添加中…' : '添加'}</Text>
                    </Button>
                  </div>
                </div>
              </div>
            </Panel>
          </div>
        </Section>
      </Fold>
    </div>
  )

  if (embedded) return content

  return (
    <Page>
      <PageHeader
        title="音频转写"
        description="选择把视频音频转成文字所用的引擎。保存后对新任务立即生效。"
      />
      {content}
    </Page>
  )
}
