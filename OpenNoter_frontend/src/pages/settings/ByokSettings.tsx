
import { useEffect, useState } from 'react'
import {
  Button,
  Divider,
  Picker,
  PickerItem,
  Radio,
  RadioGroup,
  Text,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Panel, Section } from '@/components/Page'
import { toast } from '@/lib/toast'
import { useAuthStore } from '@/store/authStore'
import { useCredentialStore } from '@/store/credentialStore'
import { updateByokConfig, type ByokMode } from '@/services/credential'


const row = style({ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 })
const hint = style({ fontSize: 'body-xs', color: 'neutral-subdued', marginTop: 4 })
const actions = style({ display: 'flex', gap: 8, marginTop: 8 })

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
