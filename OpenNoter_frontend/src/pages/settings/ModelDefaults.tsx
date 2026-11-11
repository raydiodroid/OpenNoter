import { useEffect, useState } from 'react'
import { Button, Picker, PickerItem, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { Panel } from '@/components/Page'
import { useAuthStore } from '@/store/authStore'
import request from '@/lib/api'
import { toast } from '@/lib/toast'
import { useModelStore } from '@/store/modelStore'
import type { ModelItem } from '@/types'

const stack = style({ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 640 })
interface Defaults {
  provider_id: string | null
  model_name: string | null
  models: ModelItem[]
}
export default function ModelDefaults() {
  const admin = useAuthStore(s => s.user?.is_superuser)
  const [defaults, setDefaults] = useState<Defaults | null>(null)
  const [modelId, setModelId] = useState('auto')
  const [busy, setBusy] = useState(false)
  const load = async () => {
    const d = await request.get<never, Defaults>('/model_defaults')
    setDefaults(d)
    setModelId(
      d.provider_id
        ? String(
            d.models.find(m => m.provider_id === d.provider_id && m.model_name === d.model_name)
              ?.id ?? 'unavailable'
          )
        : 'auto'
    )
  }
  useEffect(() => {
    load().catch(() => toast.error('获取默认 AI 模型失败'))
  }, [])
  if (!defaults) return <Text>加载默认 AI 模型…</Text>
  const save = async () => {
    setBusy(true)
    try {
      const m = defaults.models.find(m => String(m.id) === modelId)
      await request.patch('/model_defaults', {
        provider_id: m?.provider_id ?? null,
        model_name: m?.model_name ?? null,
      })
      await load()
      await useModelStore.getState().loadEnabled()
      toast.success('默认 AI 模型已更新')
    } catch {
      /* API 层显示错误 */
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel>
      <div className={stack}>
        <Picker
          label="站点默认 AI 模型"
          selectedKey={modelId}
          onSelectionChange={k => setModelId(String(k))}
          isDisabled={!admin || busy}
        >
          <PickerItem id="auto">自动使用首个已启用模型</PickerItem>
          {modelId === 'unavailable' && (
            <PickerItem id="unavailable">原默认模型已不可用，请重新选择</PickerItem>
          )}
          {defaults.models.map(m => (
            <PickerItem
              key={String(m.id)}
              id={String(m.id)}
            >{`${m.model_name} · ${m.provider_name || m.provider_id}`}</PickerItem>
          ))}
        </Picker>
        {admin && (
          <Button variant="accent" onPress={save} isDisabled={busy || modelId === 'unavailable'}>
            保存默认 AI 模型
          </Button>
        )}
      </div>
    </Panel>
  )
}
