/**
 * 「我的凭据」区块：模型凭据 + 平台 Cookie。
 *
 * ## 为什么不是独立页面
 *
 * 原先它是侧栏里的一个一级设置页（`/settings/profile`），但它管的两件事
 * 本来就是另外两个设置页的「个人版」：
 *
 *   我的模型凭据  ←→  模型配置页（管理员配站点默认，用户配自己的）
 *   我的平台 Cookie ←→ 下载配置页（同样分站点/个人两层）
 *
 * 独立成页的真实成本：用户在「下载配置」里看不到自己的 Cookie（那里只改站点
 * Cookie），在「模型配置」里也看不到自己的凭据 —— 必须**先想到**去第三个地方。
 * 而「我的凭据」这个名字本身也不够明确（凭据？Cookie？账号？）。
 *
 * 所以拆成两个 section，分别嵌到对应页面。侧栏因此少一个一级入口。
 *
 * ## 与 ByokSettings 的关系
 *
 * 站点模式切换仍留在 `ByokSettings`（管理员视角，不属于「个人」）。
 * 本文件只负责「我自己的」那一层。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActionButton,
  Button,
  ButtonGroup,
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

/** 嵌在「模型配置 → AI 模型」页里的个人凭据区块。整体背景见文件头注释。 */
export function MyCredentialsSection() {
  const { byokEnabled, status, load } = useCredentialStore()

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <Section title="我的模型凭据">
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
    </>
  )
}

/**
 * 嵌在「下载配置 → Cookie」页里的个人 Cookie 区块。
 *
 * 与同页的站点 Cookie 编辑器并列：站点那份是管理员给所有人用的默认值，
 * 这份是「我自己的」，配了就用我的。
 */
export function MyCookiesSection() {
  // 用 Cookie 自己的开关，不是模型的：两者是独立策略，
  // 管理员可能让用户自带模型凭据，但仍统一用站点 Cookie（或反过来）。
  const { cookieByokEnabled, cookieStatus, loadCookies } = useCredentialStore()

  useEffect(() => {
    void loadCookies()
  }, [loadCookies])

  return (
    <>
      <Section title="我的平台 Cookie">
        <Panel>
          {!cookieByokEnabled ? (
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
      {/*
        ⚠️ 按钮必须包在 <ButtonGroup> 里，不能直接当 <Dialog> 的子节点。

        S2 的 Dialog 会把 children **渲染四遍**（hero / header / content /
        footer），每遍用 context 的 isHidden 隐藏不属于该区域的部分。它只给
        `ButtonGroupContext` 在 footer 那遍设 isHidden:false —— 也就是说，
        **只有被 ButtonGroup 包裹的按钮才会出现在弹窗底部**。

        裸 <Button> 不受 ButtonGroupContext 控制，于是四遍里各渲染一次：
        实测表现为「保存/删除」跑到弹窗左上角外面、并重复出现。

        另外注意：Dialog 传了 isDismissible 时，footer 那遍的按钮组会被整个
        隐藏（`isHidden: props.isDismissible`），此时只能靠右上角 × 关闭。
        所以这里不传 isDismissible。
      */}
      <ButtonGroup>
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
      </ButtonGroup>
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
      {/* 同 CookieDialog：按钮必须包在 ButtonGroup 里，原因见那边的注释 */}
      <ButtonGroup>
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
        <Button
          variant="secondary"
          onPress={test}
          isDisabled={busy || !current?.configured}
        >
          测试连接
        </Button>
        <Button variant="accent" onPress={submit} isDisabled={busy}>
          保存
        </Button>
      </ButtonGroup>
    </Dialog>
  )
}
