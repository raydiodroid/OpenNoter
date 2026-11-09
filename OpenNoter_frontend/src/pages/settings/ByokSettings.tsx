
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

/**
 * Radio 选项的文字块。
 *
 * **两个约束，缺一个就出问题：**
 *
 * 1. **文字必须用普通 DOM 元素，不能用 S2 的 `Text`。**
 *    `Radio` 底层的 RAC `RadioField` 给 TextContext 挂的 slots 只有
 *    `description`（没有 DEFAULT_SLOT）。而 S2 的 `Text` 内部会走
 *    `useSlottedContext(TextContext, props.slot)`：上下文有 slots、但传入的
 *    slot 名（这里没传，即 DEFAULT_SLOT）不在其中时，它**直接抛异常**：
 *
 *        Error: A slot prop is required. Valid slot names are "description".
 *
 *    于是整个「模型配置」页会被 ErrorBoundary 接住变成「这个页面出错了」。
 *    只有管理员看得到（`ByokModePanel` 按 is_superuser 渲染），所以是管理员必崩。
 *    同理 `Checkbox` / `Switch` / `Tag` 也是「有 slots 无 DEFAULT_SLOT」，
 *    子节点文字一律用普通 DOM 元素。
 *
 * 2. **标题和描述必须包在同一个容器里，不能做 Radio 的两个平级子节点。**
 *    RAC `RadioField` 是个两列 grid（`[圆圈 | 文字]`），`Radio` 的 children
 *    会按 DOM 顺序各自落进网格：第一个子节点进第 2 列，**第二个子节点落到下一个
 *    空位**，于是描述把第 1 列撑宽、标题被挤到右边去（实测标题 x 从 28px 变成
 *    626px）。包一层 flex column 后整块只占一个网格位，标题才对齐圆圈右侧。
 *
 * `OnboardingPage` 的选项卡就是这个写法，这里与它保持一致。
 */
const optionText = style({ display: 'flex', flexDirection: 'column', gap: 2 })
const optionLabel = style({ font: 'ui', color: 'neutral' })
/** Radio 内的描述：间距交给 optionText 的 gap，这里不能再带 marginTop（会叠成 6px） */
const optionDesc = style({ fontSize: 'body-xs', color: 'neutral-subdued' })
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
              {/* 标题与描述必须包在同一个容器里；文字不能用 <Text>。
                  两个约束的原因见 optionText / optionLabel 的注释。 */}
              <span className={optionText}>
                <span className={optionLabel}>{opt.label}</span>
                <span className={optionDesc}>{opt.desc}</span>
              </span>
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
