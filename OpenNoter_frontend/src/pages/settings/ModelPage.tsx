import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Button,
  AlertDialog,
  DialogTrigger,
  Checkbox,
  LabeledValue,
  SearchField,
  Switch,
  Text,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { EmptyState, ListDetail, Page, PageHeader, Panel, Section } from '@/components/Page'
import { ModelConnectionFields } from '@/components/ModelConnectionFields'
import { Chip } from '@/components/Chip'
import {
  ListRow,
  ListRowBody,
  ListRowButton,
  ListRowMeta,
  ListRowTitle,
} from '@/components/ListRow'
import { ProviderIconPicker } from '@/components/ProviderIconPicker'
import { ProviderListPanel } from '@/components/ProviderListPanel'
import { PROVIDER_ICONS } from '@/lib/providerIcons'
import { ProviderMark } from '@/components/Brand'
import { useProviderStore } from '@/store/providerStore'
import { useModelStore } from '@/store/modelStore'
import { testConnection } from '@/services/model'
import { hostOf } from '@/lib/format'
import { toast } from '@/lib/toast'
import type { Provider } from '@/types'
import { Notice } from '@/components/Notice'

const formStack = style({ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 560 })
const actions = style({ display: 'flex', justifyContent: 'end', gap: 8 })
const chips = style({ display: 'flex', flexWrap: 'wrap', gap: 8 })
const remoteList = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  maxHeight: 320,
  overflowY: 'auto',
  marginTop: 8,
})
const muted = style({ font: 'detail', color: 'neutral-subdued' })
const mutedPadded = style({ font: 'detail', color: 'neutral-subdued', padding: 8 })
const mutedSpaced = style({ font: 'detail', color: 'neutral-subdued', marginTop: 12 })
const detailStack = style({ display: 'flex', flexDirection: 'column', gap: 24 })
const fullWidth = style({ width: 'full' })
const detailHead = style({ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 })
const detailTitle = style({ font: 'title', color: 'neutral', margin: 0 })

function ProviderList({ activeId, onCreate }: { activeId?: string; onCreate: () => void }) {
  const navigate = useNavigate()
  const providers = useProviderStore(s => s.providers)
  const update = useProviderStore(s => s.update)

  const isTranscriberSlot = (id: string) => id === 'groq'
  const visible = providers.filter(p => !isTranscriberSlot(p.id))

  const toggle = async (provider: Provider, next: boolean) => {
    try {
      await update({ ...provider, enabled: next ? 1 : 0 })
    } catch {
      toast.error('切换启用状态失败')
    }
  }

  const renderItem = (provider: Provider) => (
    <ListRow key={provider.id} selected={activeId === provider.id}>
      <ListRowButton
        aria-current={activeId === provider.id ? 'page' : undefined}
        onClick={() => navigate(`/settings/ai/${provider.id}`)}
      >
        <ProviderMark name={provider.logo} size={20} />
        <ListRowBody>
          <ListRowTitle>{provider.name}</ListRowTitle>
          <ListRowMeta title={provider.baseUrl}>
            {hostOf(provider.baseUrl) || '未配置地址'}
          </ListRowMeta>
        </ListRowBody>
      </ListRowButton>
      <Switch
        aria-label={`启用 ${provider.name}`}
        isSelected={provider.enabled === 1}
        onChange={next => toggle(provider, next)}
      />
    </ListRow>
  )

  return (
    <ProviderListPanel onCreate={onCreate}>
      {visible.length === 0 ? (
        <Text styles={mutedPadded}>还没有供应商</Text>
      ) : (
        visible.map(renderItem)
      )}
    </ProviderListPanel>
  )
}

function ProviderDetail({ id, isCreate }: { id?: string; isCreate?: boolean }) {
  const navigate = useNavigate()
  const fetchOne = useProviderStore(s => s.fetchOne)
  const create = useProviderStore(s => s.create)
  const update = useProviderStore(s => s.update)
  const remove = useProviderStore(s => s.remove)

  const enabledModels = useModelStore(s => s.providerModels)
  const remoteModels = useModelStore(s => s.remote)
  const loadingRemote = useModelStore(s => s.loadingRemote)
  const loadProviderModels = useModelStore(s => s.loadProviderModels)
  const loadRemote = useModelStore(s => s.loadRemote)
  const addModel = useModelStore(s => s.addModel)
  const removeModel = useModelStore(s => s.removeModel)

  const [loading, setLoading] = useState(!isCreate)
  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [type, setType] = useState('custom')
  const [logo, setLogo] = useState('custom')
  const [enabled, setEnabled] = useState(1)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [search, setSearch] = useState('')
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!id) {
        setName('')
        setApiKey('')
        setBaseUrl('')
        setType('custom')
        setLogo('custom')
        setLoading(false)
        return
      }
      setLoading(true)
      const provider = await fetchOne(id)
      if (cancelled) return
      if (provider) {
        setName(provider.name)
        setApiKey(provider.apiKey)
        setBaseUrl(provider.baseUrl)
        setType(provider.type)
        setLogo(provider.logo)
        setEnabled(provider.enabled)
      }
      await loadProviderModels(id)
      if (!cancelled) setLoading(false)
    }
    load()
    return () => {
      cancelled = true
    }
  }, [fetchOne, id, loadProviderModels])

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return remoteModels
    return remoteModels.filter(model => model.id.toLowerCase().includes(keyword))
  }, [remoteModels, search])

  const save = async () => {
    if (!name.trim()) {
      toast.error('请填写供应商名称')
      return
    }
    if (!/^https?:\/\/.+/.test(baseUrl.trim())) {
      toast.error('API 地址需要以 http:// 或 https:// 开头')
      return
    }
    setSaving(true)
    try {
      if (isCreate || !id) {
        const newId = await create({
          name: name.trim(),
          apiKey,
          baseUrl: baseUrl.trim(),
          type,
          logo,
        })
        toast.success('供应商已创建')
        navigate(`/settings/ai/${newId}`, { replace: true })
      } else {
        await update({
          id,
          name: name.trim(),
          apiKey,
          baseUrl: baseUrl.trim(),
          type,
          logo,
          enabled: useProviderStore.getState().providers.find(p => p.id === id)?.enabled ?? enabled,
        })
        toast.success('已保存')
        setDirty(false)
      }
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false)
    }
  }

  const deleteCurrent = async () => {
    if (!id) return
    setSaving(true)
    try {
      await remove(id)
      await useModelStore.getState().loadEnabled()
      toast.success('供应商已删除')
      navigate('/settings/ai', { replace: true })
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    if (!id) {
      toast.error('请先保存供应商')
      return
    }
    setTesting(true)
    try {
      await testConnection({ id })
      toast.success('连通性测试通过')
    } catch {
      // 拦截器已提示
    } finally {
      setTesting(false)
    }
  }

  const toggleModel = async (modelName: string, next: boolean) => {
    if (!id) {
      toast.error('请先保存供应商')
      return
    }
    const existing = enabledModels.find(model => model.model_name === modelName)
    try {
      if (next) {
        await addModel(id, modelName)
      } else if (existing) {
        await removeModel(existing.id, id)
      }
    } catch {
      // 拦截器已提示
    }
  }

  if (loading) {
    return (
      <Panel>
        <Text styles={muted}>加载中…</Text>
      </Panel>
    )
  }

  const isBuiltIn = type === 'built-in'

  return (
    <div className={detailStack}>
      <Panel>
        <div className={detailHead}>
          <ProviderMark name={logo} size={20} />
          <h2 className={detailTitle}>{isCreate ? '新增供应商' : name || '供应商'}</h2>
        </div>

        <div className={formStack}>
          {!isBuiltIn ? (
            <Notice variant="notice">
              <Text>
                自定义供应商需要兼容 OpenAI SDK 的接口格式（/chat/completions、/models）。
              </Text>
            </Notice>
          ) : null}

          <TextField
            label="名称"
            styles={fullWidth}
            value={name}
            onChange={value => {
              setName(value)
              setDirty(true)
            }}
            isDisabled={isBuiltIn}
            isRequired
          />
          <ProviderIconPicker
            value={logo}
            onChange={value => {
              setLogo(value)
              setDirty(true)
            }}
            isDisabled={saving}
            options={PROVIDER_ICONS}
            renderIcon={name => <ProviderMark name={name} size={20} boxed={false} />}
          />
          <ModelConnectionFields
            isDisabled={saving}
            apiKey={apiKey}
            onApiKeyChange={value => {
              setApiKey(value)
              setDirty(true)
            }}
            baseUrl={baseUrl}
            onBaseUrlChange={value => {
              setBaseUrl(value)
              setDirty(true)
            }}
          />
          <LabeledValue
            label="类型"
            value={isBuiltIn ? '内置供应商（地址与鉴权由后端维护）' : '自定义供应商（OpenAI 兼容）'}
          />

          <div className={actions}>
            {!isCreate && id && !isBuiltIn && (
              <DialogTrigger>
                <Button variant="negative" isDisabled={saving}>
                  删除供应商
                </Button>
                <AlertDialog
                  title="删除供应商"
                  variant="destructive"
                  primaryActionLabel="删除"
                  cancelLabel="取消"
                  onPrimaryAction={() => {
                    void deleteCurrent().catch(() => {})
                  }}
                >
                  删除 {name}
                  、连接凭据及该供应商的模型配置？历史笔记会保留。若其为站点默认，请先切换默认模型。
                </AlertDialog>
              </DialogTrigger>
            )}
            {!isCreate ? (
              <Button variant="secondary" onPress={test} isDisabled={testing || !id}>
                {testing ? '测试中…' : '测试连通'}
              </Button>
            ) : null}
            <Button variant="accent" onPress={save} isDisabled={saving || (!isCreate && !dirty)}>
              {saving ? '保存中…' : isCreate ? '创建' : '保存修改'}
            </Button>
          </div>
        </div>
      </Panel>

      {!isCreate && id ? (
        <>
          <Section
            title="已启用模型"
            actions={
              <Button variant="secondary" onPress={() => loadRemote(id)} isDisabled={loadingRemote}>
                {loadingRemote ? '拉取中…' : '拉取模型列表'}
              </Button>
            }
          >
            <Panel>
              {enabledModels.length === 0 ? (
                <Text styles={muted}>还没有启用任何模型，先点右上角拉取供应商模型。</Text>
              ) : (
                <div className={chips}>
                  {enabledModels.map(model => (
                    <Chip
                      key={String(model.id)}
                      onRemove={() => toggleModel(model.model_name, false)}
                      removeLabel={`移除 ${model.model_name}`}
                    >
                      {model.model_name}
                    </Chip>
                  ))}
                </div>
              )}
            </Panel>
          </Section>

          <Section title="供应商模型">
            <Panel>
              <SearchField
                aria-label="搜索模型"
                placeholder="搜索模型名…"
                value={search}
                onChange={setSearch}
              />
              {remoteModels.length === 0 ? (
                <Text styles={mutedSpaced}>点「拉取模型列表」读取该供应商支持的模型。</Text>
              ) : (
                <div className={remoteList}>
                  {filtered.map(model => {
                    const checked = enabledModels.some(item => item.model_name === model.id)
                    return (
                      <Checkbox
                        key={model.id}
                        isSelected={checked}
                        onChange={next => toggleModel(model.id, next)}
                      >
                        {model.id}
                      </Checkbox>
                    )
                  })}
                </div>
              )}
            </Panel>
          </Section>
        </>
      ) : null}
    </div>
  )
}

export default function ModelPage({
  isCreate = false,
  embedded = false,
}: {
  isCreate?: boolean
  embedded?: boolean
}) {
  const navigate = useNavigate()
  const { id } = useParams()
  const fetchAll = useProviderStore(s => s.fetchAll)

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const content = (
    <ListDetail list={<ProviderList activeId={id} onCreate={() => navigate('/settings/ai/new')} />}>
      {isCreate || id ? (
        <ProviderDetail id={id} isCreate={isCreate} />
      ) : (
        <Panel padded={false}>
          <EmptyState title="选择一个供应商">从左侧选择要编辑的供应商，或新建一个。</EmptyState>
        </Panel>
      )}
    </ListDetail>
  )

  if (embedded) return content

  return (
    <Page>
      <PageHeader
        title="AI 模型"
        description="供应商是模型来源；启用后模型才会出现在生成页的下拉里。"
      />
      {content}
    </Page>
  )
}
