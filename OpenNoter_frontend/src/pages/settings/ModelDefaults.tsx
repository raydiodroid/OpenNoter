import { useEffect, useState } from 'react'
import { Button, Picker, PickerItem, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { Panel, Section } from '@/components/Page'
import { useAuthStore } from '@/store/authStore'
import request from '@/lib/api'
import { toast } from '@/lib/toast'
import {
  getTranscriberConfig,
  getModelsStatus,
  updateTranscriberConfig,
  type TranscriberConfig,
} from '@/services/transcriber'
import { useModelStore } from '@/store/modelStore'
import type { ModelItem } from '@/types'

const stack = style({ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 640 })
interface Defaults {
  provider_id: string | null
  model_name: string | null
  models: ModelItem[]
}
export default function ModelDefaults() {
  const admin = useAuthStore(s => s.user?.is_superuser)
  const [defaults, setDefaults] = useState<Defaults | null>(null)
  const [asr, setAsr] = useState<TranscriberConfig | null>(null)
  const [modelId, setModelId] = useState('auto')
  const [asrId, setAsrId] = useState('')
  const [effective, setEffective] = useState<Record<
    string,
    { source: string; model_name?: string; error?: string }
  > | null>(null)
  const [busy, setBusy] = useState(false)
  const load = async () => {
    const [d, a, e] = await Promise.all([
      request.get<never, Defaults>('/model_defaults'),
      getTranscriberConfig(),
      request.get<never, Record<string, { source: string; model_name?: string; error?: string }>>(
        '/selection_status'
      ),
    ])
    setEffective(e)
    setDefaults(d)
    setAsr(a)
    setModelId(
      d.provider_id
        ? String(
            d.models.find(m => m.provider_id === d.provider_id && m.model_name === d.model_name)
              ?.id ?? 'unavailable'
          )
        : 'auto'
    )
    setAsrId(a.transcriber_type)
  }
  useEffect(() => {
    load().catch(() => toast.error('获取全局默认配置失败'))
  }, [])
  if (!defaults || !asr) return <Text>加载全局默认配置…</Text>
  const saveModel = async () => {
    setBusy(true)
    try {
      const m = defaults.models.find(m => String(m.id) === modelId)
      await request.patch('/model_defaults', {
        provider_id: m?.provider_id ?? null,
        model_name: m?.model_name ?? null,
      })
      await load()
      await useModelStore.getState().loadEnabled()
      toast.success('全局 AI 默认模型已更新')
    } catch {
      // API 层已显示具体错误，保留表单供重试。
    } finally {
      setBusy(false)
    }
  }
  const saveAsr = async () => {
    setBusy(true)
    try {
      if (asrId === 'fast-whisper' || asrId === 'mlx-whisper') {
        const status = await getModelsStatus()
        const models = asrId === 'mlx-whisper' ? status.mlx_whisper : status.whisper
        if (!models.some(m => m.model_size === asr.whisper_model_size && m.downloaded)) {
          toast.error('请先在「音频转写」中下载并选择本地模型')
          return
        }
      }
      await updateTranscriberConfig({ transcriber_type: asrId })
      await load()
      toast.success('全局默认转写已更新')
    } catch {
      // API 层已显示具体错误，保留表单供重试。
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={stack}>
      <Section
        title="全局默认"
        description="新任务与 AI 问答在未指定模型时使用这些默认值；个人凭据按站点 BYOK 策略优先生效。"
      >
        <Panel>
          <div className={stack}>
            {effective && (
              <>
                <Text>{`当前账号自动 AI：${effective.llm.error || `${effective.llm.source === 'own' ? '个人' : '站点'} · ${effective.llm.model_name}`}`}</Text>
                <Text>{`当前账号自动转写：${effective.asr.error || `${effective.asr.source === 'own' ? '个人' : '站点'} · ${effective.asr.model_name}`}`}</Text>
              </>
            )}
            <Picker
              label="站点默认 AI 模型"
              selectedKey={modelId}
              onSelectionChange={k => setModelId(String(k))}
              isDisabled={!admin || busy}
            >
              <PickerItem id="auto">自动使用首个已启用模型（兼容旧配置）</PickerItem>
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
              <Button
                variant="accent"
                isDisabled={busy || modelId === 'unavailable'}
                onPress={saveModel}
              >
                保存默认 AI 模型
              </Button>
            )}
            <Picker
              label="站点默认转写"
              selectedKey={asrId}
              onSelectionChange={k => setAsrId(String(k))}
              isDisabled={!admin || busy}
            >
              {asr.providers.map(p => (
                <PickerItem
                  key={p.id}
                  id={p.id}
                  isDisabled={!p.enabled || (p.id === 'mlx-whisper' && !asr.mlx_whisper_available)}
                >{`${p.name}${p.enabled ? '' : '（已停用）'}`}</PickerItem>
              ))}
            </Picker>
            {admin && (
              <Button
                variant="accent"
                isDisabled={busy || !asrId || asrId === asr.transcriber_type}
                onPress={saveAsr}
              >
                保存默认转写
              </Button>
            )}
            <Text>
              模型按供应商与名称一起选择；明确选中的来源不会被另一份密钥覆盖。默认模型被停用或删除时会报错，请重新选择。
            </Text>
            <Text>
              转写模型、下载状态和连接参数在「音频转写」中配置。只使用字幕的任务不会调用转写服务。
            </Text>
          </div>
        </Panel>
      </Section>
    </div>
  )
}
