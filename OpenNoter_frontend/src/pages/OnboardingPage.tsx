import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Heading, Radio, RadioGroup, Text, TextField } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import logo from '@/assets/icon.svg'
import {
  addModel,
  addProvider,
  getProviderList,
  testConnection,
  updateProvider,
} from '@/services/model'
import { updateTranscriberConfig } from '@/services/transcriber'
import { Notice } from '@/components/Notice'

const ONBOARD_KEY = 'opennoter-onboarded'
const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

function errorText(error: any): string {
  if (!error) return '未知错误'
  if (typeof error === 'string') return error
  return error.msg || error.message || JSON.stringify(error)
}

function backendBase(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL as string | undefined
  return (fromEnv && fromEnv.length > 0 ? fromEnv : '/api').replace(/\/$/, '')
}

async function pingBackend(): Promise<boolean> {
  try {
    const res = await fetch(`${backendBase()}/sys_check`)
    if (!res.ok) return false
    const json = await res.json().catch(() => null)
    return json?.code === 0
  } catch {
    return false
  }
}

const page = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: 'screen',
  padding: 24,
  backgroundColor: 'layer-1',
})

const card = style({
  width: 560,
  boxSizing: 'border-box',
  maxWidth: 'full',
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
  padding: 32,
  borderRadius: 'xl',
  backgroundColor: 'base',
  boxShadow: 'emphasized',
})

const head = style({ display: 'flex', alignItems: 'center', gap: 12 })
const headTitle = style({ font: 'heading-sm', color: 'neutral', margin: 0 })
const headSub = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const logoMark = style({ borderRadius: 'default' })
const headText = style({ display: 'flex', flexDirection: 'column', gap: 2 })
const stepRow = style({ display: 'flex', alignItems: 'center', gap: 8 })
const stepItem = style({ display: 'flex', alignItems: 'center', gap: 8 })
const stepDot = style({
  width: 24,
  height: 24,
  borderRadius: 'full',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  font: 'detail-sm',
  fontWeight: 'bold',
  backgroundColor: 'gray-200',
  color: 'neutral-subdued',
})
const stepDotActive = style({ backgroundColor: 'accent-900', color: 'white' })
const stepLine = style({ width: 24, height: 1, backgroundColor: 'gray-300' })
const section = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const actions = style({ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 4 })
const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const optionCard = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  paddingTop: 4,
  paddingBottom: 8,
})
const optionTitle = style({ font: 'ui', color: 'neutral' })
const optionDesc = style({ font: 'detail-sm', color: 'neutral-subdued' })

export default function OnboardingPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  const [error, setError] = useState('')

  const [pinging, setPinging] = useState(false)
  const [backendOk, setBackendOk] = useState<boolean | null>(null)

  const [providerName, setProviderName] = useState('OpenAI')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [modelName, setModelName] = useState('gpt-4o-mini')
  const [savingProvider, setSavingProvider] = useState(false)

  const [transcriberType, setTranscriberType] = useState('groq')
  const [savingTranscriber, setSavingTranscriber] = useState(false)

  const doPing = useCallback(async () => {
    setPinging(true)
    const ok = await pingBackend()
    setBackendOk(ok)
    setPinging(false)
    return ok
  }, [])

  useEffect(() => {
    if (step !== 1) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let offReady: (() => void) | null = null
    let offRestarted: (() => void) | null = null

    ;(async () => {
      const ok = await doPing()
      if (cancelled) return
      if (!ok) {
        timer = setTimeout(() => {
          if (!cancelled) doPing()
        }, 2000)
      }

      if (isTauri) {
        try {
          const { listen } = await import('@tauri-apps/api/event')
          offReady = await listen('backend-ready', () => {
            if (!cancelled) doPing()
          })
          offRestarted = await listen('backend-restarted', () => {
            if (!cancelled) doPing()
          })
        } catch {
          // 拿不到事件 API 不致命
        }
      }
    })()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      offReady?.()
      offRestarted?.()
    }
  }, [doPing, step])

  const saveProvider = async () => {
    setError('')
    if (!providerName.trim() || !baseUrl.trim() || !apiKey.trim() || !modelName.trim()) {
      setError('四项都必填：供应商名、API 地址、API Key、模型名')
      return
    }
    setSavingProvider(true)
    try {
      const name = providerName.trim()
      let providerId: string | undefined

      try {
        providerId = await addProvider(
          {
            name,
            api_key: apiKey.trim(),
            base_url: baseUrl.trim(),
            type: 'custom',
            logo: 'custom',
          },
          { silent: true }
        )
      } catch (addError: any) {
        if (!errorText(addError).includes('已存在')) throw addError
        // 后端预置了同名供应商：复用并更新凭据
        const list = (await getProviderList({ silent: true })) || []
        const existing = list.find(provider => provider.name === name)
        if (!existing?.id) throw new Error(`供应商「${name}」已存在但无法定位，请换个名字`)
        providerId = String(existing.id)
        await updateProvider(
          {
            id: providerId,
            api_key: apiKey.trim(),
            base_url: baseUrl.trim(),
            enabled: 1,
          },
          { silent: true }
        )
      }

      if (!providerId) throw new Error('后端未返回 provider id')

      try {
        await addModel({ provider_id: providerId, model_name: modelName.trim() }, { silent: true })
      } catch (modelError: any) {
        if (!errorText(modelError).includes('已存在')) throw modelError
      }

      try {
        await testConnection({ id: providerId, model: modelName.trim() }, { silent: true })
      } catch {
        // 连通性失败不阻断引导
      }

      setStep(3)
    } catch (err: any) {
      setError(`保存失败：${errorText(err)}`)
    } finally {
      setSavingProvider(false)
    }
  }

  const saveTranscriber = async () => {
    setError('')
    setSavingTranscriber(true)
    try {
      const needsSize = transcriberType === 'fast-whisper' || transcriberType === 'mlx-whisper'
      await updateTranscriberConfig({
        transcriber_type: transcriberType,
        ...(needsSize ? { whisper_model_size: 'tiny' } : {}),
      })
      setStep(4)
    } catch (err: any) {
      setError(`保存失败：${errorText(err)}`)
    } finally {
      setSavingTranscriber(false)
    }
  }

  const finish = () => {
    localStorage.setItem(ONBOARD_KEY, '1')
    navigate('/', { replace: true })
  }

  return (
    <div className={page}>
      <div className={card}>
        <div className={head}>
          <img src={logo} alt="" width={40} height={40} className={logoMark} />
          <div className={headText}>
            <h1 className={headTitle}>欢迎使用 OpenNoter</h1>
            <p className={headSub}>几步配置后就可以把视频转成笔记。</p>
          </div>
        </div>

        <div className={stepRow}>
          {[1, 2, 3, 4].map(value => (
            <div key={value} className={stepItem}>
              <span className={step >= value ? `${stepDot} ${stepDotActive}` : stepDot}>
                {value}
              </span>
              {value < 4 ? <span className={stepLine} /> : null}
            </div>
          ))}
        </div>

        {step === 1 ? (
          <section className={section}>
            <Heading level={2}>第 1 步 · 后端连通性</Heading>
            <p className={muted}>桌面端会自动启动 Python 后端进程，首次启动需要 10–30 秒。</p>
            {pinging ? (
              <Notice variant="informative">
                <Text>检测中…</Text>
              </Notice>
            ) : backendOk === true ? (
              <Notice variant="positive">
                <Text>后端已就绪</Text>
              </Notice>
            ) : backendOk === false ? (
              <Notice variant="negative">
                <Text>
                  暂时连不上后端。可能正在初始化，等 1–2 分钟再试；右下角的状态点会持续监控。
                </Text>
              </Notice>
            ) : null}
            <div className={actions}>
              <Button variant="secondary" onPress={doPing} isDisabled={pinging}>
                {pinging ? '检测中…' : '重新检测'}
              </Button>
              <Button variant="accent" onPress={() => setStep(2)} isDisabled={backendOk !== true}>
                下一步
              </Button>
            </div>
          </section>
        ) : null}

        {step === 2 ? (
          <section className={section}>
            <Heading level={2}>第 2 步 · 模型供应商</Heading>
            <p className={muted}>
              填一个 OpenAI 兼容供应商：DeepSeek / Qwen / Claude / 自托管 / OpenAI 都行。
            </p>
            <TextField label="供应商名（自取）" value={providerName} onChange={setProviderName} />
            <TextField label="API 地址" value={baseUrl} onChange={setBaseUrl} />
            <TextField label="API Key" type="password" value={apiKey} onChange={setApiKey} />
            <TextField
              label="模型名"
              value={modelName}
              onChange={setModelName}
              placeholder="gpt-4o-mini / deepseek-chat / qwen-turbo"
            />
            {error ? (
              <Notice variant="negative">
                <Text>{error}</Text>
              </Notice>
            ) : null}
            <div className={actions}>
              <Button variant="secondary" onPress={() => setStep(1)}>
                上一步
              </Button>
              <Button variant="accent" onPress={saveProvider} isDisabled={savingProvider}>
                {savingProvider ? '保存中…' : '保存并下一步'}
              </Button>
            </div>
          </section>
        ) : null}

        {step === 3 ? (
          <section className={section}>
            <Heading level={2}>第 3 步 · 音频转写引擎</Heading>
            <p className={muted}>把视频音频转成文字。推荐在线引擎，避免本地下载数百 MB 的模型。</p>
            <RadioGroup aria-label="转写引擎" value={transcriberType} onChange={setTranscriberType}>
              <Radio value="groq">
                <span className={optionCard}>
                  <span className={optionTitle}>Groq（在线，推荐）</span>
                  <span className={optionDesc}>速度快、英文语料佳，无需本地模型。</span>
                </span>
              </Radio>
              <Radio value="bcut">
                <span className={optionCard}>
                  <span className={optionTitle}>必剪（在线，免登）</span>
                  <span className={optionDesc}>中文表现好，偶尔限流。</span>
                </span>
              </Radio>
              <Radio value="kuaishou">
                <span className={optionCard}>
                  <span className={optionTitle}>快手（在线，免登）</span>
                  <span className={optionDesc}>与必剪类似，作为备选。</span>
                </span>
              </Radio>
              <Radio value="fast-whisper">
                <span className={optionCard}>
                  <span className={optionTitle}>Faster Whisper（本地）</span>
                  <span className={optionDesc}>完全离线，首次需下载 75MB–3GB 模型，CPU 较慢。</span>
                </span>
              </Radio>
            </RadioGroup>
            {error ? (
              <Notice variant="negative">
                <Text>{error}</Text>
              </Notice>
            ) : null}
            <div className={actions}>
              <Button variant="secondary" onPress={() => setStep(2)}>
                上一步
              </Button>
              <Button variant="accent" onPress={saveTranscriber} isDisabled={savingTranscriber}>
                {savingTranscriber ? '保存中…' : '保存并下一步'}
              </Button>
            </div>
          </section>
        ) : null}

        {step === 4 ? (
          <section className={section}>
            <Heading level={2}>第 4 步 · Cookie 同步（可选）</Heading>
            <p className={muted}>
              想总结 B 站 / 抖音 / 快手等需要登录态的平台时，需要把浏览器 Cookie
              复制到「下载配置」页。 YouTube 一般不需要，先跳过也没问题。
            </p>
            <Notice variant="informative">
              <Text>浏览器插件版支持一键同步 Cookie；桌面版需要手动复制。</Text>
            </Notice>
            <div className={actions}>
              <Button variant="secondary" onPress={() => setStep(3)}>
                上一步
              </Button>
              <Button variant="accent" onPress={finish}>
                完成，进入 OpenNoter
              </Button>
            </div>
          </section>
        ) : null}

        {error && step === 1 ? (
          <Notice variant="negative">
            <Text>{error}</Text>
          </Notice>
        ) : null}
      </div>
    </div>
  )
}
