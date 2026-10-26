import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Button, Text, TextField } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import logo from '@/assets/icon.svg'
import { useAuthStore } from '@/store/authStore'
import { oidcLoginUrl } from '@/lib/urls'
import { Notice } from '@/components/Notice'

const page = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: 'screen',
  padding: 24,
  backgroundColor: 'layer-1',
})

const card = style({
  width: 360,
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

const brand = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 8,
  textAlign: 'center',
  marginBottom: 4,
})

const brandName = style({ font: 'heading-sm', color: 'neutral', margin: 0 })
const hint = style({ font: 'detail', color: 'neutral-subdued', textAlign: 'center', margin: 0 })
const divider = style({
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  marginY: 4,
})
const line = style({ flexGrow: 1, height: 1, backgroundColor: 'gray-200' })

const logoMark = style({ borderRadius: 'lg' })

const formStack = style({ display: 'flex', flexDirection: 'column', gap: 12 })

const fullWidth = style({ width: 'full' })

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const login = useAuthStore(s => s.login)
  const oidcEnabled = useAuthStore(s => s.oidcEnabled)
  const navigate = useNavigate()
  const location = useLocation()

  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || '/'

  const submit = async () => {
    if (!email.trim() || !password) {
      setError('请填写邮箱和密码')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await login(email.trim(), password)
      navigate(from, { replace: true })
    } catch (err: any) {
      setError(err?.msg || err?.data?.detail || '登录失败，请检查邮箱和密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={page}>
      <div className={card}>
        <div className={brand}>
          <img src={logo} alt="" width={48} height={48} className={logoMark} />
          <h1 className={brandName}>OpenNoter</h1>
          <Text styles={hint}>登录后使用</Text>
        </div>

        {error ? (
          <Notice variant="negative">
            <Text>{error}</Text>
          </Notice>
        ) : null}

        <form
          className={formStack}
          onSubmit={event => {
            event.preventDefault()
            submit()
          }}
        >
          <TextField
            label="邮箱"
            type="email"
            autoComplete="username"
            value={email}
            onChange={setEmail}
            placeholder="you@example.com"
          />
          <TextField
            label="密码"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
            placeholder="••••••••"
          />
          <Button type="submit" variant="accent" isDisabled={loading} styles={fullWidth}>
            {loading ? '登录中…' : '登录'}
          </Button>
        </form>

        {oidcEnabled ? (
          <>
            <div className={divider}>
              <div className={line} />
              <Text styles={hint}>或</Text>
              <div className={line} />
            </div>
            <Button
              variant="secondary"
              onPress={() => {
                // 桌面端必须用 sidecar 的绝对地址：Tauri webview 的页面源是
                // `tauri://localhost`，相对路径 `/api/...` 会被解析成
                // `tauri://localhost/api/...`（没有这个服务），OIDC 直接白屏。
                // Web 端保持同源相对路径，让浏览器正常跟随 302 到 IdP。
                window.location.href = oidcLoginUrl()
              }}
              styles={fullWidth}
            >
              使用统一身份登录
            </Button>
          </>
        ) : null}

        <Text styles={hint}>账号由管理员创建，忘记密码请联系管理员。</Text>
      </div>
    </div>
  )
}
