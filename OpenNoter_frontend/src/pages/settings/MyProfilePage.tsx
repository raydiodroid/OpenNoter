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
  Text,
  TextArea,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Panel, Section } from '@/components/Page'
import { toast } from '@/lib/toast'
import { useCredentialStore } from '@/store/credentialStore'
import {
  COOKIE_PLATFORM_META,
  discoverModels,
  testMyCredential,
  type CookiePlatformStatus,
} from '@/services/credential'

/**
 * 个人凭据管理页：把「模型凭据 + 平台 Cookie」放在一起。
 *
 * ## 为什么合并成一页
 *
 * 这两件事在用户心里是同一件事——「我用自己的账号和额度，而不是站点的」。
 * 分成两个页面会让用户找不到 Cookie 的入口（它比凭据更隐蔽，因为影响的是
 * 画质/字幕而不是「能不能用」）。
 *
 * ## 与旧版 ByokSettings 的关系
 *
 * 旧的 `ByokSettings.tsx` 只做了凭据（模型），本文件把它和 Cookie 一起收口。
 * 站点模式切换仍留在 `ByokSettings`（那是管理员视角，不属于「个人」页面）。
 */

const row = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  paddingY: 8,
})
const hint = style({ fontSize: 'body-xs', color: 'neutral-subdued', marginTop: 4 })
const stack = style({ display: 'flex', flexDirection: 'column', gap: 4 })
const platformRow = style({
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr) auto',
  alignItems: 'center',
  gap: 12,
})
const badgeOwn = style({ fontSize: 'body-xs', fontWeight: 'bold', color: 'accent-900' })
const badgeShared = style({ fontSize: 'body-xs', fontWeight: 'bold', color: 'neutral-subdued' })
const badgeNone = style({ fontSize: 'body-xs', fontWeight: 'bold', color: 'negative-900' })

type Capability = 'llm' | 'asr'

const CAPABILITY_META: Record<
  Capability,
  { label: string; desc: string }
> = {
  llm: {
    label: '笔记分析与 AI 问答',
    desc: '生成笔记、回答关于笔记的问题时使用的大模型',
  },
  asr: {
    label: '音频转写',
    desc: '把视频音频转成文字。未配置时跟随「笔记分析」或站点配置',
  },
}

export default function MyProfilePage() {
  const { byokEnabled, status, cookieStatus, load, loadCookies } = useCredentialStore()

  useEffect(() => {
    void load()
    void loadCookies()
  }, [load, loadCookies])

  return (
    <>
      <Section title="模型凭据">
        <Panel>
          {!byokEnabled ? (
            <p className={hint}>
              当前站点统一使用管理员配置的模型凭据，无需单独设置。
            </p>
          ) : (
            <>
              <p className={hint}>
                配置你自己的模型接口与 API Key。未配置的能力位使用站点共享配置。
              </p>
              <Divider size="S" />
              {(['llm', 'asr'] as Capability[]).map(cap => {
                const item = status?.capabilities?.find(c => c.capability === cap)
                const own = item?.source === 'own'
                return (
                  <div key={cap} className={row}>
                    <div className={stack}>
                      <Text>{CAPABILITY_META[cap].label}</Text>
                      <span className={hint}>
                        {own ? (
                          <>
                            <span className={badgeOwn}>我的</span>
                            {` ${item?.masked_key ?? ''}`}
                            {item?.model_name ? ` · ${item.model_name}` : ''}
                          </>
                        ) : (
                          <>
                            <span className={badgeShared}>站点共享</span>
                            {item?.broken ? '（凭据无法解密，请重新填写）' : ''}
                          </>
                        )}
                      </span>
                    </div>
                    <DialogTrigger>
                      <ActionButton>{own ? '修改' : '配置'}</ActionButton>
                      <CapabilityDialog capability={cap} />
                    </DialogTrigger>
                  </div>
                )
              })}
            </>
          )}
        </Panel>
      </Section>

      <Section title="平台 Cookie">
        <Panel>
          {!byokEnabled ? (
            <p className={hint}>
              当前站点统一使用管理员配置的 Cookie，无需单独设置。
            </p>
          ) : (
            <>
              <p className={hint}>
                配自己的 Cookie 可以拿到更高画质与字幕，且不会和站点的其他用户
                共用登录态。不配置时会回退到站点 Cookie（若站点有），否则按无 Cookie
                下载普通画质。
              </p>
              <Divider size="S" />
              {(cookieStatus?.platforms ?? []).map(item => (
                <CookieRow key={item.platform} item={item} />
              ))}
            </>
          )}
        </Panel>
      </Section>
    </>
  )
}

/**
 * 把后端算好的过期信息翻译成给人看的一句话。
 *
 * 三档措辞必须区分清楚：
 *  - 「已过期」   —— 有确定的过期时间且已过（仅 netscape）
 *  - 「可能失效」 —— 没过期时间，但保存太久了（header 格式）
 *  - 「还剩 N 天」 —— 有确定的过期时间且未过
 *
 * 不区分会让用户以为「没提示过期就是好的」，而 Cookie 实际可能早就失效了。
 */
function describeCookieExpiry(item: CookiePlatformStatus): string {
  if (item.expired) {
    const at = item.expires_at ? new Date(item.expires_at).toLocaleDateString() : ''
    return at ? `已过期（${at}）` : '已过期'
  }
  if (item.expiry_kind === 'known' && item.expires_at) {
    const days = Math.ceil(
      (new Date(item.expires_at).getTime() - Date.now()) / 86_400_000
    )
    return days <= 7 ? `还剩 ${days} 天过期` : ''
  }
  if (item.stale && typeof item.age_days === 'number') {
    return `已保存 ${Math.round(item.age_days)} 天，可能已失效`
  }
  if (typeof item.age_days === 'number' && item.age_days >= 1) {
    return `已保存 ${Math.round(item.age_days)} 天`
  }
  return ''
}

function CookieRow({ item }: { item: CookiePlatformStatus }) {
  const meta = COOKIE_PLATFORM_META[item.platform]
  const sourceLabel =
    item.source === 'own'
      ? { cls: badgeOwn, text: '我的' }
      : item.source === 'shared'
        ? { cls: badgeShared, text: '站点' }
        : { cls: badgeNone, text: '未配置' }

  return (
    <div className={platformRow}>
      <div className={stack}>
        <Text>{meta.label}</Text>
        <span className={hint}>
          <span className={sourceLabel.cls}>{sourceLabel.text}</span>
          {item.source === 'own' && item.entry_count > 0
            ? ` · 约 ${item.entry_count} 条`
            : ''}
          {(() => {
            const exp = describeCookieExpiry(item)
            return exp ? ` · ${exp}` : ''
          })()}
          {item.broken ? '（无法解密，请重新填写）' : ''}
          {item.source === 'none' && !item.site_available
            ? ' · 站点也未配置，将按无 Cookie 下载'
            : ''}
        </span>
      </div>
      <DialogTrigger>
        <ActionButton>{item.configured ? '修改' : '配置'}</ActionButton>
        <CookieDialog item={item} />
      </DialogTrigger>
    </div>
  )
}

function CookieDialog({ item }: { item: CookiePlatformStatus }) {
  const meta = COOKIE_PLATFORM_META[item.platform]
  const { saveCookie, removeCookie } = useCredentialStore()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!value.trim()) {
      toast.error('请粘贴 Cookie 内容')
      return
    }
    setBusy(true)
    try {
      await saveCookie(item.platform, value.trim())
      toast.success(`${meta.label} Cookie 已保存`)
      setValue('')
    } catch (error) {
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `保存失败：${detail}` : '保存失败，请检查格式')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog>
      <Heading>{meta.label} Cookie</Heading>
      <Divider />
      <Content>
        <div className={stack}>
          <p className={hint}>{meta.hint}</p>
          {meta.format === 'netscape' && (
            <p className={hint}>
              从浏览器扩展导出 cookies.txt 后整份粘贴。格式不符会被拒绝。
            </p>
          )}
          <TextArea
            label="Cookie 内容"
            value={value}
            onChange={setValue}
            placeholder={
              meta.format === 'netscape'
                ? '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t...'
                : 'SESSDATA=xxx; bili_jct=xxx'
            }
            size="L"
          />
          <p className={hint}>内容会被加密保存，页面上只显示条目数，不回显原文。</p>
        </div>
      </Content>
      <Button variant="accent" onPress={submit} isDisabled={busy}>
        保存
      </Button>
      {item.configured && (
        <Button
          variant="negative"
          onPress={async () => {
            await removeCookie(item.platform)
            toast.success('已删除')
          }}
          isDisabled={busy}
        >
          删除
        </Button>
      )}
    </Dialog>
  )
}

/** 能力位凭据对话框。
 *
 * 包含三个动作：
 *  - 保存（三者必填；api_key 留空表示不修改）
 *  - 拉取模型列表（`POST /me/credentials/discover-models`，用尚未保存的 url+key）
 *  - 测试连接（`POST /me/credentials/{cap}/test`，用已保存的凭据真发一次）
 */
function CapabilityDialog({ capability }: { capability: Capability }) {
  const { status, save, load } = useCredentialStore()
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
        res.models.length ? `获取到 ${res.models.length} 个模型` : '该端点未返回模型，请手动填写'
      )
    } catch (error) {
      // 不少自建 OpenAI 兼容网关不实现 /v1/models，保留手动填写路径
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
    if (!apiKey.trim() && !current?.configured) {
      toast.error('请填写 API Key')
      return
    }
    setBusy(true)
    try {
      await save(capability, {
        base_url: baseUrl.trim(),
        // 留空时传掩码占位，让后端「掩码不覆盖」逻辑生效
        api_key: apiKey.trim() || current?.masked_key || '',
        model_name: modelName.trim(),
      })
      toast.success('凭据已保存')
    } catch (error) {
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `保存失败：${detail}` : '保存失败')
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
      <Heading>{CAPABILITY_META[capability].label}</Heading>
      <Divider />
      <Content>
        <div className={stack}>
          <p className={hint}>{CAPABILITY_META[capability].desc}</p>
          <TextField
            label="接口地址"
            value={baseUrl}
            onChange={setBaseUrl}
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
      <Button
        variant="secondary"
        onPress={test}
        isDisabled={busy || !current?.configured}
      >
        测试连接
      </Button>
      <Button
        variant="secondary"
        onPress={() => {
          setApiKey('')
          void load()
        }}
        isDisabled={busy}
      >
        刷新状态
      </Button>
    </Dialog>
  )
}
