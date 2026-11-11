import { useEffect, useState } from 'react'
import { Button, Picker, PickerItem, Switch, Text, TextArea } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { Panel, Section } from '@/components/Page'
import { toast } from '@/lib/toast'
import { useAuthStore } from '@/store/authStore'
import { useCredentialStore } from '@/store/credentialStore'
import { useModelStore } from '@/store/modelStore'
import {
  getByokConfig,
  updateByokConfig,
  type ByokConfig,
  type ByokMode,
} from '@/services/credential'

const stack = style({ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 680 })
const modes = [
  { id: 'shared', label: '仅使用站点配置' },
  { id: 'optional', label: '允许个人凭据' },
  { id: 'required', label: '必须使用个人凭据' },
]
export function ByokPolicyPanel() {
  const admin = useAuthStore(s => s.user?.is_superuser)
  const [saved, setSaved] = useState<ByokConfig | null>(null)
  const [draft, setDraft] = useState<ByokConfig | null>(null)
  const [addresses, setAddresses] = useState('')
  const [busy, setBusy] = useState(false)
  const reset = (cfg: ByokConfig) => {
    setSaved(cfg)
    setDraft(cfg)
    setAddresses(cfg.allowed_base_urls.join('\n'))
  }
  useEffect(() => {
    getByokConfig()
      .then(reset)
      .catch(() => toast.error('加载站点策略失败'))
  }, [])
  if (!admin || !draft || !saved) return null
  const patch = (next: Partial<ByokConfig>) => setDraft({ ...draft, ...next })
  const payload = {
    ...draft,
    allowed_base_urls: addresses
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean),
  }
  const dirty = JSON.stringify(payload) !== JSON.stringify(saved)
  const save = async () => {
    if (
      (draft.model_mode === 'required' && saved.model_mode !== 'required') ||
      (draft.asr_mode === 'required' && saved.asr_mode !== 'required') ||
      (draft.cookie_mode === 'required' && saved.cookie_mode !== 'required')
    ) {
      if (
        !window.confirm(
          '未配置对应个人凭据或平台 Cookie 的用户将无法使用相关能力。确定更新站点策略？'
        )
      )
        return
    }
    setBusy(true)
    try {
      reset(await updateByokConfig(payload))
      await useCredentialStore.getState().load()
      await useModelStore.getState().loadEnabled()
      toast.success('站点策略已更新')
    } catch {
      // API 层已显示具体错误，保留表单供重试。
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section
      title="BYOK 与身份策略"
      description="AI 模型、音频转写和下载 Cookie 分别配置。更改对后续调用生效。"
    >
      <Panel>
        <div className={stack}>
          {(['model_mode', 'asr_mode'] as const).map(field => (
            <Picker
              key={field}
              label={field === 'model_mode' ? 'AI 模型凭据模式' : '音频转写凭据模式'}
              selectedKey={draft[field]}
              onSelectionChange={k => patch({ [field]: String(k) as ByokMode })}
              isDisabled={busy}
            >
              {modes.map(m => (
                <PickerItem id={m.id} key={m.id}>
                  {m.label}
                </PickerItem>
              ))}
            </Picker>
          ))}
          <Text>
            允许个人凭据：自动模式优先使用个人配置，AI
            模型也可以明确选择站点来源。必须自带：在线调用缺少个人凭据即报错；本地 Whisper
            与内置免费转写仍可使用。已配置但不可用的凭据不会回退站点。
          </Text>
          <Switch
            isSelected={draft.asr_use_default}
            onChange={v => patch({ asr_use_default: v })}
            isDisabled={busy}
          >
            允许通用个人凭据用于转写
          </Switch>
          <Text>
            关闭后转写只读取专用 ASR 凭据。通用凭据只有在接口和模型同时支持音频转写时才适用，不会把
            AI 专用模型自动当作转写模型。
          </Text>
          <Switch
            isSelected={draft.allow_custom_base_url}
            onChange={v => patch({ allow_custom_base_url: v })}
            isDisabled={busy}
          >
            允许个人凭据使用任意安全接口地址
          </Switch>
          <TextArea
            label="允许的接口地址（每行一个）"
            value={addresses}
            onChange={setAddresses}
            isDisabled={busy}
            description="关闭任意地址后，保存、模型发现和实际调用均须匹配此列表。使用完整地址，例如 https://gateway.example/v1；空列表表示不允许任何个人接口。"
          />
          <Picker
            label="平台 Cookie 模式"
            selectedKey={draft.cookie_mode}
            onSelectionChange={k =>
              patch({ cookie_mode: String(k) as 'shared' | 'optional' | 'required' })
            }
            isDisabled={busy}
          >
            <PickerItem id="shared">仅使用站点 Cookie</PickerItem>
            <PickerItem id="optional">允许个人 Cookie</PickerItem>
            <PickerItem id="required">必须使用个人 Cookie</PickerItem>
          </Picker>
          <Switch
            isSelected={draft.allow_shared_cookie_fallback}
            onChange={v => patch({ allow_shared_cookie_fallback: v })}
            isDisabled={busy || draft.cookie_mode !== 'optional'}
          >
            未配置个人 Cookie 时允许使用站点 Cookie
          </Switch>
          <Text>
            允许个人 Cookie 时可决定是否回退站点身份；必须使用个人 Cookie
            时，缺失、损坏或已知过期均拒绝对应平台任务，不回退站点或匿名下载。本地上传不受影响。
          </Text>
          <Button variant="accent" onPress={save} isDisabled={busy || !dirty}>
            {busy ? '保存中…' : '保存站点策略'}
          </Button>
          {dirty && (
            <Button variant="secondary" onPress={() => reset(saved)} isDisabled={busy}>
              放弃修改
            </Button>
          )}
        </div>
      </Panel>
    </Section>
  )
}
