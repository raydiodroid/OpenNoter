import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import QRCode from 'qrcode'
import {
  Button,
  Switch,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  Text,
  TextArea,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import GlobeIcon from '@react-spectrum/s2/icons/Location'
import DownloadIcon from '@react-spectrum/s2/icons/Download'

import { ListDetail, Page, PageHeader, Panel } from '@/components/Page'
import {
  ListRow,
  ListRowBody,
  ListRowButton,
  ListRowMeta,
  ListRowTitle,
} from '@/components/ListRow'
import { Modal } from '@/components/Modal'
import { Notice } from '@/components/Notice'
import { PlatformMark } from '@/components/Brand'
import { DOWNLOAD_PLATFORMS } from '@/constants'
import {
  cancelBilibiliQrLogin,
  getDownloaderCookie,
  pollBilibiliQrLogin,
  requestBrowserCookieTransfer,
  startBilibiliQrLogin,
  updateDownloaderCookie,
  type DownloaderPlatform,
} from '@/services/downloader'
import { getProxyConfig, updateProxyConfig } from '@/services/proxy'
import { toast } from '@/lib/toast'

/**
 * 下载配置：Cookie 与网络两件事，用 Tabs 切换 —— 与「模型配置」页
 * （AI 模型 / 音频转写）同一套做法。左侧平台列表 + 右侧表单的
 * 「列表-详情」结构也照抄模型配置页，不另起一套。
 */

type DownloaderTab = 'cookie' | 'network'

/** 这些平台的下载器不读 Cookie（与后端 cookie_policy 的平台能力一致） */
const COOKIE_FREE_PLATFORMS = ['youtube']

const COOKIE_HINT =
  '未登录只能拿到低清晰度。优先从当前浏览器获取已登录 Cookie，也可以手动粘贴；保存时只保留下载器需要的登录项。'

const PLATFORM_HINTS: Record<string, string> = {
  bilibili: '未登录只能拿到低清晰度。B 站还支持扫码登录；保存时只保留下载器需要的登录项。',
}

const list = style({ display: 'flex', flexDirection: 'column', gap: 2, padding: 8 })
const panelBody = style({ paddingTop: 16 })
const stack = style({ display: 'flex', flexDirection: 'column', gap: 16 })
/** 表单宽度上限：与模型配置页一致，避免长表单在大屏上拉得过宽 */
const formStack = style({ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 560 })
const detailHead = style({ display: 'flex', alignItems: 'center', gap: 12 })
const detailTitle = style({ font: 'title', color: 'neutral', margin: 0 })
const actions = style({ display: 'flex', justifyContent: 'end', gap: 8 })
const maxForm = style({ maxWidth: 720 })
const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const qrContent = style({ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 })
const qrImageStyle = style({ width: 240, height: 240, imageRendering: 'pixelated' })
const qrStatusStyle = style({ font: 'body', color: 'neutral', textAlign: 'center' })

function ProxyConfigPanel() {
  const [enabled, setEnabled] = useState(false)
  const [url, setUrl] = useState('')
  const [effective, setEffective] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getProxyConfig()
      .then(config => {
        setEnabled(config.enabled)
        setUrl(config.url)
        setEffective(config.effective)
      })
      .catch(() => undefined)
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    if (enabled && !url.trim()) {
      toast.error('请填写代理地址，或关闭代理开关')
      return
    }
    setSaving(true)
    try {
      const config = await updateProxyConfig({ enabled, url: url.trim() })
      setEnabled(config.enabled)
      setUrl(config.url)
      setEffective(config.effective)
      toast.success('代理配置已保存')
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel>
      <div className={stack}>
        <div className={detailHead}>
          <GlobeIcon />
          <h2 className={detailTitle}>全局代理</h2>
        </div>
        <Text styles={muted}>作用于 AI 模型接口、转写接口与 YouTube 下载。</Text>

        {loading ? (
          <Text styles={muted}>加载中…</Text>
        ) : (
          <div className={formStack}>
            <Switch isSelected={enabled} onChange={setEnabled}>
              启用代理
            </Switch>
            <TextField
              label="代理地址"
              value={url}
              onChange={setUrl}
              isDisabled={!enabled}
              placeholder="http://127.0.0.1:7890"
              description={
                enabled
                  ? undefined
                  : effective
                    ? `当前生效（来自环境变量）：${effective}`
                    : undefined
              }
            />
            {enabled && effective ? <Text styles={muted}>当前生效：{effective}</Text> : null}
            <div className={actions}>
              <Button variant="secondary" onPress={save} isDisabled={saving}>
                {saving ? '保存中…' : '保存代理配置'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

function CookieEditor({
  platformId,
  onCookieChange,
}: {
  platformId: string
  onCookieChange: (platformId: string, hasCookie: boolean) => void
}) {
  const platform = DOWNLOAD_PLATFORMS.find(item => item.value === platformId)
  const usesCookie = !COOKIE_FREE_PLATFORMS.includes(platformId)
  const [cookie, setCookie] = useState('')
  const [loading, setLoading] = useState(usesCookie)
  const [saving, setSaving] = useState(false)
  const [acquiring, setAcquiring] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [qrOpen, setQrOpen] = useState(false)
  const [qrSessionId, setQrSessionId] = useState<string | null>(null)
  const [qrImage, setQrImage] = useState('')
  const [qrStatus, setQrStatus] = useState<
    'creating' | 'waiting' | 'scanned' | 'success' | 'expired' | 'failed'
  >('creating')
  const qrGeneration = useRef(0)

  useEffect(() => {
    if (!usesCookie) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    getDownloaderCookie(platformId)
      .then(res => {
        if (cancelled) return
        const value = res?.cookie || ''
        setCookie(value)
        setDirty(false)
        onCookieChange(platformId, Boolean(value))
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [onCookieChange, platformId, usesCookie])

  useEffect(() => {
    if (!qrOpen || !qrSessionId) return
    let cancelled = false
    let timer = 0
    const poll = async () => {
      try {
        const status = await pollBilibiliQrLogin(qrSessionId)
        if (cancelled) return
        setQrStatus(status)
        if (status === 'success') {
          const saved = await getDownloaderCookie('bilibili')
          if (!cancelled) {
            setCookie(saved?.cookie || '')
            setDirty(false)
            onCookieChange('bilibili', Boolean(saved?.cookie))
            toast.success('B 站 Cookie 已更新')
          }
          return
        }
        if (status === 'expired' || status === 'failed') return
      } catch {
        if (!cancelled) setQrStatus('failed')
        return
      }
      timer = window.setTimeout(poll, 1800)
    }
    timer = window.setTimeout(poll, 800)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [onCookieChange, qrOpen, qrSessionId])

  const save = async () => {
    setSaving(true)
    try {
      await updateDownloaderCookie({ platform: platformId as DownloaderPlatform, cookie })
      toast.success('Cookie 已保存')
      setDirty(false)
      onCookieChange(platformId, Boolean(cookie.trim()))
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false)
    }
  }

  const acquireFromBrowser = async () => {
    setAcquiring(true)
    try {
      const count = await requestBrowserCookieTransfer(platformId as DownloaderPlatform)
      const saved = await getDownloaderCookie(platformId)
      setCookie(saved?.cookie || '')
      setDirty(false)
      onCookieChange(platformId, Boolean(saved?.cookie))
      toast.success(`已获取并保存 ${count} 条 Cookie`)
    } catch (error) {
      toast.error((error as Error).message || '浏览器 Cookie 获取失败')
    } finally {
      setAcquiring(false)
    }
  }

  const closeQr = () => {
    qrGeneration.current += 1
    setQrOpen(false)
    setQrImage('')
    const sessionId = qrSessionId
    setQrSessionId(null)
    if (sessionId) void cancelBilibiliQrLogin(sessionId).catch(() => undefined)
  }

  const openQr = async () => {
    const generation = ++qrGeneration.current
    setQrOpen(true)
    setQrImage('')
    setQrStatus('creating')
    try {
      const session = await startBilibiliQrLogin()
      if (generation !== qrGeneration.current) {
        void cancelBilibiliQrLogin(session.session_id).catch(() => undefined)
        return
      }
      setQrSessionId(session.session_id)
      const image = await QRCode.toDataURL(session.qr_url, {
        errorCorrectionLevel: 'M',
        margin: 2,
        width: 240,
      })
      if (generation === qrGeneration.current) {
        setQrImage(image)
        setQrStatus('waiting')
      }
    } catch (error) {
      if (generation === qrGeneration.current) {
        setQrStatus('failed')
        toast.error((error as Error).message || '二维码生成失败')
      }
    }
  }

  const qrStatusLabel = {
    creating: '正在生成二维码…',
    waiting: '请使用哔哩哔哩 App 扫码登录',
    scanned: '已扫码，请在手机上确认登录',
    success: '登录成功，Cookie 已安全保存到后端',
    expired: '二维码已过期，请关闭后重新获取',
    failed: '登录失败，请关闭后重试',
  }[qrStatus]

  return (
    <>
      <Panel>
        <div className={stack}>
          <div className={detailHead}>
            {platform ? <PlatformMark Logo={platform.Logo} size={20} /> : null}
            <h2 className={detailTitle}>{platform?.label || platformId} 下载器</h2>
          </div>

          {!usesCookie ? (
            <div className={formStack}>
              <Notice variant="neutral">
                <Text>YouTube 下载器当前不使用 Cookie，无需配置。</Text>
              </Notice>
            </div>
          ) : (
            <>
              {loading ? (
                <Text styles={muted}>加载 Cookie…</Text>
              ) : (
                <div className={formStack}>
                  <Notice variant="neutral">
                    <Text>{PLATFORM_HINTS[platformId] || COOKIE_HINT}</Text>
                  </Notice>

                  <TextArea
                    label="Cookie"
                    value={cookie}
                    onChange={value => {
                      setCookie(value)
                      setDirty(true)
                    }}
                    placeholder="粘贴浏览器开发者工具里复制的 Cookie"
                    description="保存时只保留该平台下载器需要的登录项，其余统计与偏好 Cookie 会被丢弃。"
                    UNSAFE_className="cookie-textarea"
                  />
                  <div className={actions}>
                    {platformId === 'bilibili' ? (
                      <Button
                        variant="secondary"
                        onPress={openQr}
                        isDisabled={saving || acquiring}
                      >
                        B 站扫码登录
                      </Button>
                    ) : null}
                    <Button
                      variant="secondary"
                      onPress={acquireFromBrowser}
                      isDisabled={saving || acquiring}
                    >
                      {acquiring ? '正在获取…' : '从当前浏览器获取'}
                    </Button>
                    <Button variant="accent" onPress={save} isDisabled={saving || acquiring || !dirty}>
                      {saving ? '保存中…' : '保存 Cookie'}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </Panel>

      <Modal open={qrOpen} label="B 站扫码登录" width={420} onClose={closeQr}>
        <div className={qrContent}>
          <h2 className={detailTitle}>哔哩哔哩扫码登录</h2>
          {qrImage ? <img className={qrImageStyle} src={qrImage} alt="哔哩哔哩登录二维码" /> : null}
          <Text styles={qrStatusStyle}>{qrStatusLabel}</Text>
          <Text styles={muted}>
            二维码约 3 分钟有效。登录凭据由后端直接保存，不会返回到页面脚本。
          </Text>
          <Button variant="secondary" onPress={closeQr}>
            关闭
          </Button>
        </div>
      </Modal>
    </>
  )
}

export default function DownloaderPage() {
  const navigate = useNavigate()
  const { id } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeId = id || 'bilibili'
  const tab: DownloaderTab = searchParams.get('tab') === 'network' ? 'network' : 'cookie'
  const [cookieStatus, setCookieStatus] = useState<Record<string, boolean>>({})

  /** 切页写进 URL，方便直接分享「网络」面板的链接 */
  const setTab = useCallback(
    (next: DownloaderTab) => {
      setSearchParams(
        prev => {
          const params = new URLSearchParams(prev)
          if (next === 'cookie') params.delete('tab')
          else params.set('tab', next)
          return params
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const handleCookieChange = useCallback((platformId: string, hasCookie: boolean) => {
    setCookieStatus(prev =>
      prev[platformId] === hasCookie ? prev : { ...prev, [platformId]: hasCookie },
    )
  }, [])

  /** 左侧列表要显示各平台是否已配置 Cookie */
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const entries = await Promise.all(
        DOWNLOAD_PLATFORMS.filter(item => !COOKIE_FREE_PLATFORMS.includes(item.value)).map(
          async item => {
            try {
              const res = await getDownloaderCookie(item.value, { suppressToast: true })
              return [item.value, Boolean(res?.cookie)] as const
            } catch {
              return [item.value, false] as const
            }
          },
        ),
      )
      if (cancelled) return
      setCookieStatus(Object.fromEntries(entries))
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const platformList = (
    <Panel padded={false}>
      <div className={list} role="navigation" aria-label="下载平台">
        {DOWNLOAD_PLATFORMS.map(platform => {
          const active = activeId === platform.value
          const cookieFree = COOKIE_FREE_PLATFORMS.includes(platform.value)
          const meta = cookieFree
            ? '无需 Cookie'
            : cookieStatus[platform.value]
              ? '已配置'
              : '未配置'
          return (
            <ListRow key={platform.value} selected={active}>
              <ListRowButton
                aria-current={active ? 'page' : undefined}
                onClick={() => navigate(`/settings/download/${platform.value}`)}
              >
                <PlatformMark Logo={platform.Logo} size={20} />
                <ListRowBody>
                  <ListRowTitle>{platform.label}</ListRowTitle>
                  <ListRowMeta>{meta}</ListRowMeta>
                </ListRowBody>
              </ListRowButton>
            </ListRow>
          )
        })}
      </div>
    </Panel>
  )

  return (
    <Page>
      <PageHeader
        title="下载配置"
        description="需要登录态的视频（B 站、抖音、快手）填入 Cookie 后才能拿到高清原片。首次使用浏览器获取功能，请先安装扩展。"
        actions={
          <Button
            variant="secondary"
            onPress={() => {
              window.location.href = '/OpenNoter-extension.zip'
            }}
          >
            <DownloadIcon />
            下载扩展（Chrome / Edge）
          </Button>
        }
      />

      <Tabs
        aria-label="下载配置"
        selectedKey={tab}
        onSelectionChange={key => setTab(String(key) as DownloaderTab)}
      >
        <TabList aria-label="下载配置">
          <Tab id="cookie">Cookie</Tab>
          <Tab id="network">网络</Tab>
        </TabList>

        <TabPanel id="cookie">
          <div className={panelBody}>
            <ListDetail list={platformList}>
              <CookieEditor platformId={activeId} onCookieChange={handleCookieChange} />
            </ListDetail>
          </div>
        </TabPanel>

        <TabPanel id="network">
          <div className={panelBody}>
            <div className={maxForm}>
              <ProxyConfigPanel />
            </div>
          </div>
        </TabPanel>
      </Tabs>
    </Page>
  )
}
