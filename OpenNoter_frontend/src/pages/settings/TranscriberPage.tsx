import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActionButton,
  AlertDialog,
  Button,
  ComboBox,
  ComboBoxItem,
  Cell,
  Column,
  Content,
  DialogTrigger,
  Heading,
  IllustratedMessage,
  LabeledValue,
  Picker,
  PickerItem,
  Row,
  Switch,
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

import { ListDetail, Page, PageHeader, Panel, Section } from '@/components/Page'
import { ModelConnectionFields } from '@/components/ModelConnectionFields'
import {
  ListRow,
  ListRowBody,
  ListRowButton,
  ListRowMeta,
  ListRowTitle,
} from '@/components/ListRow'
import { ProviderIconPicker } from '@/components/ProviderIconPicker'
import { ProviderListPanel } from '@/components/ProviderListPanel'
import { AsrMark } from '@/components/AsrMark'
import { ASR_ICONS } from '@/lib/asrIcons'
import { hostOf } from '@/lib/format'
import { StatusIndicator } from '@/components/StatusIndicator'
import {
  addWhisperModel,
  deleteWhisperModel,
  downloadModel,
  getModelsStatus,
  getTranscriberConfig,
  discoverAsrModels,
  createAsrProvider,
  updateAsrProvider,
  deleteAsrProvider,
  type AsrProvider,
  updateTranscriberConfig,
  type ModelStatus,
  type TranscriberConfig,
} from '@/services/transcriber'
import { useAuthStore } from '@/store/authStore'
import { toast } from '@/lib/toast'
import { Notice } from '@/components/Notice'
import { Fold } from '@/components/Fold'

const isWhisperType = (type: string) => type === 'fast-whisper' || type === 'mlx-whisper'

// 无 gap：间距由各子项自带，Fold 收起时随之消失（见 Fold.tsx 注释）
const settingsStack = style({ display: 'flex', flexDirection: 'column', gap: 24 })
const defaultStack = style({ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 640 })
const pageStack = style({ display: 'flex', flexDirection: 'column' })
const maxForm = style({ width: 'full' })
const detailHead = style({ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 })
const detailTitle = style({ font: 'title', color: 'neutral', margin: 0 })
const formStack = style({ display: 'flex', flexDirection: 'column', maxWidth: 560, gap: 16 })
const modelSection = style({ paddingTop: 32 })
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
  const admin = useAuthStore(s => s.user?.is_superuser)
  const [defaultType, setDefaultType] = useState('')
  const [config, setConfig] = useState<TranscriberConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const [selectedType, setSelectedType] = useState('')
  const [selectedSize, setSelectedSize] = useState('')
  const [whisperStatus, setWhisperStatus] = useState<ModelStatus[]>([])
  const [mlxStatus, setMlxStatus] = useState<ModelStatus[]>([])
  const [mlxAvailable, setMlxAvailable] = useState(false)

  const [providerName, setProviderName] = useState('')
  const [providerLogo, setProviderLogo] = useState('cloud')
  const [asrBaseUrl, setAsrBaseUrl] = useState('')
  const [asrApiKey, setAsrApiKey] = useState('')
  const [asrModel, setAsrModel] = useState('')
  const [remoteModels, setRemoteModels] = useState<string[]>([])
  const [discovering, setDiscovering] = useState(false)
  const [discoveryHint, setDiscoveryHint] = useState('')
  const discoveryVersion = useRef(0)
  useEffect(() => {
    discoveryVersion.current++
    setRemoteModels([])
    setDiscovering(false)
    setDiscoveryHint('')
  }, [selectedType, asrBaseUrl, asrApiKey])

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
      const data = await getTranscriberConfig()
      setConfig(data)
      setDefaultType(data.transcriber_type)
    } catch {
      // 静默
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const data = await getTranscriberConfig()
        setConfig(data)
        setDefaultType(data.transcriber_type)
        const selected = data.providers.find(p => p.id === data.transcriber_type)
        if (selected) selectProvider(selected)
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

  function selectProvider(provider: AsrProvider) {
    setSelectedType(provider.id)
    setProviderName(provider.name)
    setProviderLogo(provider.logo)
    setAsrBaseUrl(provider.base_url)
    setAsrModel(provider.model_name)
    setAsrApiKey('')
  }

  const startCreate = () => {
    setSelectedType('new')
    setProviderName('')
    setProviderLogo('cloud')
    setAsrBaseUrl('')
    setAsrModel('')
    setAsrApiKey('')
  }
  const selectedProvider = config?.providers.find(p => p.id === selectedType)
  const isCreate = selectedType === 'new'
  const isOnline = isCreate || selectedProvider?.engine === 'groq'

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
    if (!isWhisperType(defaultType)) return false
    const statuses = defaultType === 'mlx-whisper' ? mlxStatus : whisperStatus
    const target = statuses.find(model => model.model_size === config?.whisper_model_size)
    return !target?.downloaded && !target?.downloading
  }

  const save = async () => {
    if (!providerName.trim()) {
      toast.error('请填写供应商名称')
      return
    }
    if (isOnline && (!/^https?:\/\/.+/.test(asrBaseUrl.trim()) || !asrModel.trim())) {
      toast.error('请填写有效的 API 地址和模型名称')
      return
    }
    setSaving(true)
    try {
      const details = { name: providerName.trim(), logo: providerLogo }
      const connection = {
        base_url: asrBaseUrl.trim(),
        model_name: asrModel.trim(),
        api_key: asrApiKey.trim() || undefined,
      }
      const provider = isCreate
        ? await createAsrProvider({ ...details, ...connection })
        : await updateAsrProvider(selectedType, { ...details, ...(isOnline ? connection : {}) })
      if (isWhisperType(selectedType) && selectedSize !== config?.whisper_model_size) {
        await updateTranscriberConfig({
          transcriber_type: config!.transcriber_type,
          whisper_model_size: selectedSize,
        })
      }
      selectProvider(provider)
      await reloadConfig()
      toast.success(isCreate ? '供应商已添加，可设为默认使用' : '供应商已保存')
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const setDefault = async () => {
    setSaving(true)
    try {
      await updateTranscriberConfig({
        transcriber_type: defaultType,
      })
      await reloadConfig()
      toast.success('默认转写供应商已更新')
    } catch {
      toast.error('切换默认供应商失败')
    } finally {
      setSaving(false)
    }
  }

  const discover = async () => {
    const version = ++discoveryVersion.current
    setDiscovering(true)
    try {
      const result = await discoverAsrModels({
        base_url: asrBaseUrl.trim(),
        api_key: asrApiKey.trim(),
        provider_id: isCreate ? undefined : selectedType,
      })
      if (version !== discoveryVersion.current) return
      setRemoteModels(result.models)
      setDiscoveryHint(
        result.models.length
          ? '请选择支持音频转写的模型；列表可能包含其他类型。'
          : '未返回模型，可手动填写模型名称。'
      )
    } catch {
      if (version === discoveryVersion.current) {
        setRemoteModels([])
        setDiscoveryHint('拉取失败，仍可手动填写模型名称。')
      }
    } finally {
      if (version === discoveryVersion.current) setDiscovering(false)
    }
  }

  const toggle = async (provider: AsrProvider, enabled: boolean) => {
    setSaving(true)
    try {
      await updateAsrProvider(provider.id, { enabled })
      await reloadConfig()
      if (!enabled && provider.id === config?.transcriber_type)
        toast.info('默认供应商已停用，请选择其他默认供应商')
    } catch {
      toast.error('切换启用状态失败')
    } finally {
      setSaving(false)
    }
  }

  const removeProvider = async () => {
    setSaving(true)
    try {
      await deleteAsrProvider(selectedType)
      const data = await getTranscriberConfig()
      setConfig(data)
      setDefaultType(data.transcriber_type)
      selectProvider(data.providers.find(p => p.id === data.transcriber_type) || data.providers[0])
      toast.success('供应商已删除')
    } catch {
      toast.error('删除失败')
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

  const dirty =
    isCreate ||
    providerName.trim() !== selectedProvider?.name ||
    providerLogo !== selectedProvider?.logo ||
    (isWhisperType(selectedType) && selectedSize !== config.whisper_model_size) ||
    (isOnline &&
      (asrBaseUrl.trim() !== selectedProvider?.base_url ||
        asrModel.trim() !== selectedProvider?.model_name ||
        Boolean(asrApiKey.trim())))
  const engineList = (
    <ProviderListPanel onCreate={startCreate} isDisabled={saving}>
      {config.providers.map(provider => (
        <ListRow key={provider.id} selected={selectedType === provider.id}>
          <ListRowButton
            aria-current={selectedType === provider.id ? 'true' : undefined}
            onClick={() => selectProvider(provider)}
            disabled={saving}
          >
            <AsrMark name={provider.logo} />
            <ListRowBody>
              <ListRowTitle>{provider.name}</ListRowTitle>
              <ListRowMeta>
                {provider.engine === 'groq'
                  ? hostOf(provider.base_url) || '未配置地址'
                  : isWhisperType(provider.id)
                    ? '本地模型'
                    : '内置在线服务'}
              </ListRowMeta>
            </ListRowBody>
          </ListRowButton>
          <Switch
            aria-label={`启用 ${provider.name}`}
            isSelected={provider.enabled}
            isDisabled={saving}
            onChange={enabled => toggle(provider, enabled)}
          />
        </ListRow>
      ))}
    </ProviderListPanel>
  )

  const content = (
    <div className={settingsStack}>
      <Panel>
        <div className={defaultStack}>
          <Picker
            label="站点默认音频转写"
            selectedKey={defaultType}
            onSelectionChange={key => setDefaultType(String(key))}
            isDisabled={!admin || saving}
          >
            {config.providers.map(provider => (
              <PickerItem
                key={provider.id}
                id={provider.id}
                textValue={provider.name}
                isDisabled={!provider.enabled || (provider.id === 'mlx-whisper' && !mlxAvailable)}
              >
                <Text slot="label">{`${provider.name}${provider.model_name ? ` · ${provider.model_name}` : ''}`}</Text>
              </PickerItem>
            ))}
          </Picker>
          {admin && (
            <DialogTrigger
              isOpen={confirmOpen}
              onOpenChange={open => {
                if (!open) setConfirmOpen(false)
              }}
            >
              <Button
                variant="accent"
                onPress={() => {
                  if (needsModelDownload()) setConfirmOpen(true)
                  else void setDefault()
                }}
                isDisabled={
                  saving ||
                  !config.providers.find(p => p.id === defaultType)?.enabled ||
                  (defaultType === 'mlx-whisper' && !mlxAvailable)
                }
              >
                保存默认音频转写
              </Button>
              <AlertDialog
                title="所选模型尚未下载"
                variant="warning"
                primaryActionLabel="继续保存"
                cancelLabel="取消"
                onPrimaryAction={() => {
                  setConfirmOpen(false)
                  void setDefault()
                }}
              >
                请先下载 {config.whisper_model_size}{' '}
                模型。下载完成前，需要本地转写的新任务会提示模型未就绪。
              </AlertDialog>
            </DialogTrigger>
          )}
        </div>
      </Panel>
      <ListDetail list={engineList}>
        <div className={pageStack}>
          <div>
            <div className={maxForm}>
              <Panel>
                <div className={detailHead}>
                  <AsrMark name={providerLogo} />
                  <h2 className={detailTitle}>
                    {isCreate ? '添加供应商' : selectedProvider?.name || '转写供应商'}
                  </h2>
                </div>
                <div className={formStack}>
                  <TextField
                    label="供应商名称"
                    value={providerName}
                    onChange={setProviderName}
                    isRequired
                    isDisabled={saving}
                    styles={maxForm}
                  />
                  <ProviderIconPicker
                    value={providerLogo}
                    onChange={setProviderLogo}
                    isDisabled={saving}
                    options={ASR_ICONS}
                    renderIcon={name => <AsrMark name={name} boxed={false} />}
                  />
                  {selectedProvider && !selectedProvider.enabled && (
                    <Notice variant="notice">
                      <Text>此供应商已停用。启用后才能设为默认或用于转写。</Text>
                    </Notice>
                  )}
                  {isOnline && (
                    <>
                      <Notice variant="informative">
                        <Text>
                          任意 OpenAI 兼容的语音转写服务（调用 /audio/transcriptions），可填 Groq
                          云端，也可填自建网关。
                        </Text>
                      </Notice>
                      <ModelConnectionFields
                        apiKey={asrApiKey}
                        onApiKeyChange={setAsrApiKey}
                        keyPlaceholder={
                          selectedProvider?.has_api_key
                            ? '已配置，留空则不修改'
                            : '尚未配置，请填写'
                        }
                        baseUrl={asrBaseUrl}
                        onBaseUrlChange={setAsrBaseUrl}
                        addressPlaceholder="https://api.groq.com/openai/v1"
                        isDisabled={saving}
                      />
                      <ComboBox
                        label="模型名称"
                        inputValue={asrModel}
                        onInputChange={setAsrModel}
                        allowsCustomValue
                        isDisabled={saving}
                        placeholder="选择或输入模型名称"
                        styles={maxForm}
                      >
                        {remoteModels.map(model => (
                          <ComboBoxItem key={model} id={model} textValue={model}>
                            <Text slot="label">{model}</Text>
                          </ComboBoxItem>
                        ))}
                      </ComboBox>
                      <Button
                        variant="secondary"
                        onPress={() => void discover()}
                        isDisabled={saving || discovering || !asrBaseUrl.trim()}
                      >
                        {discovering ? '拉取中…' : '拉取模型列表'}
                      </Button>
                      {discoveryHint && <Text styles={muted}>{discoveryHint}</Text>}
                    </>
                  )}

                  {isWhisperType(selectedType) && (
                    <>
                      <Picker
                        label="Whisper 模型大小"
                        isDisabled={saving}
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
                  )}

                  <LabeledValue
                    label="类型"
                    value={
                      isWhisperType(selectedType)
                        ? '本地转写引擎'
                        : isOnline
                          ? '在线服务（OpenAI 兼容）'
                          : '在线转写引擎'
                    }
                  />

                  {selectedType === 'mlx-whisper' && !config.mlx_whisper_available ? (
                    <div className={spacedAfterFirst}>
                      <Notice variant="notice">
                        <AlertIcon />
                        <Text>
                          MLX Whisper 当前不可用：需要 macOS 并安装
                          mlx-whisper，安装后重启后端生效。
                        </Text>
                      </Notice>
                    </div>
                  ) : null}

                  <div className={actions}>
                    {selectedProvider && !selectedProvider.builtin && (
                      <DialogTrigger>
                        <Button
                          variant="negative"
                          isDisabled={saving || selectedType === config.transcriber_type}
                        >
                          删除供应商
                        </Button>
                        <AlertDialog
                          title="删除供应商"
                          variant="destructive"
                          primaryActionLabel="删除"
                          cancelLabel="取消"
                          onPrimaryAction={() => void removeProvider()}
                        >
                          删除 {selectedProvider.name} 及其连接凭据？历史笔记会保留。
                        </AlertDialog>
                      </DialogTrigger>
                    )}
                    <Button
                      variant="accent"
                      onPress={() => void save()}
                      isDisabled={saving || !dirty}
                    >
                      {saving ? '保存中…' : isCreate ? '添加供应商' : '保存修改'}
                    </Button>
                  </div>
                </div>
              </Panel>
            </div>
          </div>

          <Fold open={isWhisperType(selectedType)}>
            <div className={modelSection}>
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
            </div>
          </Fold>

          <Fold open={selectedType === 'fast-whisper'}>
            <div className={modelSection}>
              <Section title="自定义模型">
                <div className={maxForm}>
                  <Panel>
                    <div className={formStack}>
                      <Text styles={muted}>
                        填 HF repo_id（如
                        Systran/faster-whisper-large-v3，添加后手动下载）或本地模型目录（目录内需含
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
            </div>
          </Fold>
        </div>
      </ListDetail>
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
