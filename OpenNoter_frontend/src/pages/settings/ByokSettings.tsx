import { useCallback, useEffect, useState } from 'react'
import {
  ActionButton,
  Button,
  Content,
  Dialog,
  DialogTrigger,
  Divider,
  Heading,
  Picker,
  PickerItem,
  Radio,
  RadioGroup,
  Text,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Panel, Section } from '@/components/Page'
import { toast } from '@/lib/toast'
import { useAuthStore } from '@/store/authStore'
import { useCredentialStore } from '@/store/credentialStore'
import {
  discoverModels,
  testMyCredential,
  updateByokConfig,
  type ByokMode,
} from '@/services/credential'

const row = style({ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 })
const hint = style({ fontSize: 'body-xs', color: 'neutral-subdued', marginTop: 4 })
const formGrid = style({ display: 'grid', gridTemplateColumns: '1fr', gap: 12 })
const actions = style({ display: 'flex', gap: 8, marginTop: 8 })
const badge = style({ fontSize: 'body-xs', fontWeight: 'bold' })

const MODE_OPTIONS: { id: ByokMode; label: string; desc: string }[] = [
  {
    id: 'shared',
    label: '仅使用站点配置',
    desc: '所有用户共用管理员配置的模型。与升级前行为完全一致。',
  },
  {
    id: 'optional',
    label: '允许用户自带（推荐）',
    desc: '站点配置是默认档位；用户可配置自己的凭据，配了就用自己的。',
  },
  {
    id: 'required',
    label: '强制用户自带',
    desc: '未配置自有凭据的用户将无法提交任务。适用于平台不承担 API 成本的场景。',
  },
]

/**
 * 站点级 BYOK 模式切换（管理员）。
 *
 * 切换**立即生效**（后端写 config/byok.json 并失效缓存），不需要重启。
 * 从 optional 切到 required 是破坏性变更，所以给二次确认。
 */
export function ByokModePanel() {
  const user = useAuthStore(s => s.user)
  const { mode, allowCustomBaseUrl, load } = useCredentialStore()
  const [saving, setSaving] = useState(false)
  const [draftMode, setDraftMode] = useState<ByokMode>(mode)
  const [draftAllowCustom, setDraftAllowCustom] = useState(allowCustomBaseUrl)

  useEffect(() => {
    setDraftMode(mode)
    setDraftAllowCustom(allowCustomBaseUrl)
  }, [mode, allowCustomBaseUrl])

  if (!user?.is_superuser) return null

  const dirty = draftMode !== mode || draftAllowCustom !== allowCustomBaseUrl

  const save = async () => {
    if (draftMode === 'required' && mode !== 'required') {
      const ok = window.confirm(
        '「强制用户自带」会让所有尚未配置凭据的用户立刻无法提交任务。确定切换？'
      )
      if (!ok) return
    }
    setSaving(true)
    try {
      await updateByokConfig({ mode: draftMode, allow_custom_base_url: draftAllowCustom })
      await load()
      toast.success('站点 BYOK 模式已更新')
    } catch (error) {
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `更新失败：${detail}` : '更新失败，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="模型凭据来源（站点设置）">
      <Panel>
        <RadioGroup
          aria-label="BYOK 模式"
          value={draftMode}
          onChange={value => setDraftMode(value as ByokMode)}
        >
          {MODE_OPTIONS.map(opt => (
            <Radio key={opt.id} value={opt.id}>
              <Text>{opt.label}</Text>
              <div className={hint}>{opt.desc}</div>
            </Radio>
          ))}
        </RadioGroup>

        <Divider size="S" />

        <div className={row}>
          <div>
            <Text>允许用户自定义接口地址</Text>
            <div className={hint}>
              关闭后用户只能填 API Key（接口地址由站点决定）。当前无内置地址白名单时，
              关闭此项等价于禁止用户保存凭据。
            </div>
          </div>
          <Picker
            aria-label="允许自定义接口地址"
            selectedKey={draftAllowCustom ? 'yes' : 'no'}
            onSelectionChange={k => setDraftAllowCustom(String(k) === 'yes')}
          >
            <PickerItem id="yes">允许</PickerItem>
            <PickerItem id="no">不允许</PickerItem>
          </Picker>
        </div>

        <div className={actions}>
          <Button variant="accent" onPress={save} isDisabled={!dirty || saving}>
            {saving ? '保存中…' : '保存'}
          </Button>
          {dirty && (
            <Button
              variant="secondary"
              onPress={() => {
                setDraftMode(mode)
                setDraftAllowCustom(allowCustomBaseUrl)
              }}
              isDisabled={saving}
            >
              撤销
            </Button>
          )}
        </div>
      </Panel>
    </Section>
  )
}

const CAP_LABEL: Record<'llm' | 'asr', string> = {
  llm: '笔记生成与 AI 问答',
  asr: '音频转写',
}

/**
 * 用户自己的凭据管理。
 *
 * 每个能力位一行，显示「当前：我的 / 站点共享」——这是 BYOK 的核心价值感知：
 * 用户必须随时知道这次调用花的是谁的钱。
 */
export function MyCredentialsPanel() {
  const { byokEnabled, status, allowCustomBaseUrl, load } = useCredentialStore()
  const [editing, setEditing] = useState<'llm' | 'asr' | null>(null)

  useEffect(() => {
    if (!status) void load()
  }, [status, load])

  if (!byokEnabled) return null

  return (
    <Section title="我的凭据">
      <Panel>
        <p className={hint}>
          配置你自己的模型接口与 API Key。未配置时使用站点共享配置；
          配置后该能力位优先使用你自己的。
        </p>
        <Divider size="S" />
        {(status?.capabilities ?? []).map(cap => {
          const own = cap.source === 'own'
          return (
            <div key={cap.capability} className={row}>
              <div>
                <Text>{CAP_LABEL[cap.capability]}</Text>
                <div className={hint}>
                  {own ? (
                    <>
                      当前：<span className={badge}>{cap.masked_key}</span>
                      {cap.model_name ? ` · ${cap.model_name}` : ''}
                    </>
                  ) : (
                    '当前：站点共享配置'
                  )}
                  {cap.broken ? '（凭据无法解密，请重新填写）' : ''}
                </div>
              </div>
              <DialogTrigger>
                <ActionButton onPress={() => setEditing(cap.capability)}>
                  {own ? '修改' : '配置'}
                </ActionButton>
                <CredentialDialog
                  capability={cap.capability}
                  allowCustomBaseUrl={allowCustomBaseUrl}
                  onDone={() => {
                    setEditing(null)
                    void load()
                  }}
                />
              </DialogTrigger>
            </div>
          )
        })}
        {editing === null && null}
      </Panel>
    </Section>
  )
}

/** 单个能力位的凭据编辑对话框。 */
function CredentialDialog({
  capability,
  allowCustomBaseUrl,
  onDone,
}: {
  capability: 'llm' | 'asr'
  allowCustomBaseUrl: boolean
  onDone: () => void
}) {
  const { status, save, remove } = useCredentialStore()
  const current = status?.credentials?.find(c => c.capability === capability)

  const [baseUrl, setBaseUrl] = useState(current?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [modelName, setModelName] = useState(current?.model_name ?? '')
  const [models, setModels] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const discover = useCallback(async () => {
    if (!baseUrl.trim() || !apiKey.trim()) {
      toast.error('请先填写接口地址和 API Key')
      return
    }
    setBusy(true)
    try {
      const res = await discoverModels({ base_url: baseUrl.trim(), api_key: apiKey.trim() })
      setModels(res.models)
      if (res.models.length === 1) setModelName(res.models[0])
      toast.success(
        res.models.length
          ? `获取到 ${res.models.length} 个模型`
          : '该端点未返回模型，请手动填写'
      )
    } catch (error) {
      // 不少自建网关不实现 /v1/models，保留手动填写的路径
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `拉取失败：${detail}` : '拉取失败，请手动填写模型名称')
    } finally {
      setBusy(false)
    }
  }, [baseUrl, apiKey])

  const submit = async () => {
    if (!baseUrl.trim() || !modelName.trim()) {
      toast.error('请填写接口地址与模型名称')
      return
    }
    // 已有凭据时允许留空 api_key（表示不修改），否则必填
    if (!apiKey.trim() && !current?.configured) {
      toast.error('请填写 API Key')
      return
    }
    setBusy(true)
    try {
      await save(capability, {
        base_url: baseUrl.trim(),
        // 留空时传掩码占位，让后端的「掩码不覆盖」逻辑生效
        api_key: apiKey.trim() || current?.masked_key || '',
        model_name: modelName.trim(),
      })
      toast.success('凭据已保存')
      onDone()
    } catch (error) {
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `保存失败：${detail}` : '保存失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    setBusy(true)
    try {
      const res = await testMyCredential(capability)
      toast.success(`连接成功（${res.latency_ms}ms，模型 ${res.model}）`)
    } catch (error) {
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `测试失败：${detail}` : '测试失败，请检查配置')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog>
      <Heading>{CAP_LABEL[capability]}</Heading>
      <Divider />
      <Content>
        <div className={formGrid}>
          <TextField
            label="接口地址"
            value={baseUrl}
            onChange={setBaseUrl}
            isDisabled={!allowCustomBaseUrl}
            placeholder="https://api.deepseek.com/v1"
            description="OpenAI 兼容端点，通常以 /v1 结尾"
          />
          <TextField
            label="API Key"
            type="password"
            value={apiKey}
            onChange={setApiKey}
            placeholder={current?.configured ? `${current.masked_key}（留空则不修改）` : 'sk-...'}
          />
          <div className={row}>
            <TextField label="模型名称" value={modelName} onChange={setModelName} />
            <ActionButton onPress={discover} isDisabled={busy}>
              拉取列表
            </ActionButton>
          </div>
          {models.length > 0 && (
            <Picker
              aria-label="可选模型"
              selectedKey={modelName || null}
              onSelectionChange={k => setModelName(String(k))}
            >
              {models.map(m => (
                <PickerItem key={m} id={m}>
                  {m}
                </PickerItem>
              ))}
            </Picker>
          )}
        </div>
      </Content>
      <Button variant="accent" onPress={submit} isDisabled={busy}>
        保存
      </Button>
      <Button variant="secondary" onPress={test} isDisabled={busy || !current?.configured}>
        测试连接
      </Button>
      {current?.configured && (
        <Button
          variant="negative"
          onPress={async () => {
            await remove(capability)
            onDone()
          }}
          isDisabled={busy}
        >
          删除（回到站点默认）
        </Button>
      )}
    </Dialog>
  )
}
