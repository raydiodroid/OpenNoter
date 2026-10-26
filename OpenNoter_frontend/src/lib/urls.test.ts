import { describe, expect, it, vi } from 'vitest'

/**
 * `lib/urls.ts` 的回归测试（C2 / F37）。
 *
 * ## 为什么这个文件重要
 *
 * 在此之前前端**没有任何测试设施** —— `package.json` 没有 `test` script，
 * `node_modules/.bin` 里没有 vitest。于是 `staticUrl` / `apiOrigin` /
 * `ownStaticPath` 这些「改错了只会在某个部署形态下才炸」的函数**零回归保护**。
 *
 * 已经踩过的两个坑，正好说明为什么必须有这层：
 *   1. `import.meta.env.VITE_API_BASE_URL` 在裸 node 下会抛 TypeError
 *      （`import.meta.env` 是 undefined）—— 改成可选链才修好；
 *   2. `isOwnBackendStaticUrl` 早期只判 loopback，会漏掉「同源但非 loopback」
 *      的部署（`http://my-host/static/...`），导致那些部署的封面仍走代理。
 *
 * ## 怎么模拟不同运行环境
 *
 * `urls.ts` 的 `API_BASE` 是**模块级常量**，由 `import.meta.env` 决定。
 * 所以每个场景都要 `vi.resetModules()` 后重新 import，并在 import 前
 * 设置好 `window` / `__TAURI_INTERNALS__`。
 */

/** 在指定环境下重新加载 `lib/urls.ts`。 */
async function loadUrls(opts: {
  apiBase?: string
  href?: string
  tauri?: boolean
}) {
  vi.resetModules()

  // 模拟 Vite 注入的 import.meta.env
  vi.stubEnv('VITE_API_BASE_URL', opts.apiBase ?? '')

  const href = opts.href ?? 'http://localhost:3015/'
  const fakeWindow: Record<string, unknown> = {
    location: {
      href,
      host: new URL(href).host,
      origin: new URL(href).origin,
      pathname: new URL(href).pathname,
      hash: '',
    },
  }
  if (opts.tauri) fakeWindow.__TAURI_INTERNALS__ = {}

  vi.stubGlobal('window', fakeWindow)

  return await import('../lib/urls')
}

describe('apiOrigin / staticUrl — Web（同源）模式', () => {
  it('API_BASE=/api 时没有独立 origin，静态路径保持相对', async () => {
    const u = await loadUrls({ apiBase: '' })
    // 未设 VITE_API_BASE_URL 时回退 '/api'
    expect(u.API_BASE).toBe('/api')
    expect(u.apiOrigin()).toBe('')
    expect(u.staticUrl('/static/a.jpg')).toBe('/static/a.jpg')
  })

  it('保留 query（截图令牌 ?t= 靠这条才能透传）', async () => {
    const u = await loadUrls({ apiBase: '' })
    const signed = '/static/screenshots/t/shot.jpg?t=abc123'
    expect(u.staticUrl(signed)).toBe(signed)
  })
})

describe('apiOrigin / staticUrl — Tauri（绝对地址）模式', () => {
  it('从绝对 API_BASE 推出 sidecar origin', async () => {
    const u = await loadUrls({ apiBase: 'http://127.0.0.1:8483/api', tauri: true })
    expect(u.API_BASE).toBe('http://127.0.0.1:8483/api')
    expect(u.apiOrigin()).toBe('http://127.0.0.1:8483')
  })

  it('staticUrl 把站内路径补成绝对地址', async () => {
    const u = await loadUrls({ apiBase: 'http://127.0.0.1:8483/api', tauri: true })
    expect(u.staticUrl('/static/x.jpg')).toBe('http://127.0.0.1:8483/static/x.jpg')
  })

  it('**令牌必须原样保留**（丢了就是 403 破图）', async () => {
    const u = await loadUrls({ apiBase: 'http://127.0.0.1:8483/api', tauri: true })
    expect(u.staticUrl('/static/screenshots/t/a.jpg?t=abc123')).toBe(
      'http://127.0.0.1:8483/static/screenshots/t/a.jpg?t=abc123',
    )
  })

  it('已经是绝对地址的输入原样返回', async () => {
    const u = await loadUrls({ apiBase: 'http://127.0.0.1:8483/api', tauri: true })
    expect(u.staticUrl('https://i0.hdslb.com/a.jpg')).toBe('https://i0.hdslb.com/a.jpg')
    expect(u.staticUrl('data:image/png;base64,AAA')).toBe('data:image/png;base64,AAA')
  })

  it('OIDC 登录走 sidecar 绝对地址（Tauri webview 里相对路径会白屏）', async () => {
    const u = await loadUrls({ apiBase: 'http://127.0.0.1:8483/api', tauri: true })
    expect(u.oidcLoginUrl()).toBe('http://127.0.0.1:8483/api/auth/oidc/login')
  })
})

describe('ownStaticPath / isOwnBackendStaticUrl（X1 封面分流）', () => {
  it('站内相对路径直接命中', async () => {
    const u = await loadUrls({})
    expect(u.ownStaticPath('/static/cover/x.jpg')).toBe('/static/cover/x.jpg')
    expect(u.isOwnBackendStaticUrl('/static/cover/x.jpg')).toBe(true)
  })

  it.each([
    ['http://localhost:8483/static/cover/x.jpg'],
    ['http://127.0.0.1:8483/static/cover/x.jpg'],
    ['http://0.0.0.0:8483/static/cover/x.jpg'],
  ])('loopback 绝对地址判为自己：%s', async (url) => {
    const u = await loadUrls({ href: 'http://localhost:3015/' })
    expect(u.isOwnBackendStaticUrl(url)).toBe(true)
  })

  it('**同源但非 loopback** 也要判为自己（早期实现的漏判点）', async () => {
    const u = await loadUrls({ href: 'http://my-host:3015/' })
    expect(u.isOwnBackendStaticUrl('http://my-host:3015/static/cover/x.jpg')).toBe(true)
  })

  it.each([
    ['https://i0.hdslb.com/bfs/archive/a.jpg'],
    ['https://i.ytimg.com/vi/x/hq.jpg'],
    ['https://evil.com/static/cover/x.jpg'],
    ['https://evil-bilibili.com/static/x.jpg'],
  ])('外部地址不判为自己：%s', async (url) => {
    const u = await loadUrls({ href: 'http://localhost:3015/' })
    expect(u.isOwnBackendStaticUrl(url)).toBe(false)
  })

  it('保留 query（ownStaticPath 返回 pathname+search）', async () => {
    const u = await loadUrls({ href: 'http://my-host:3015/' })
    expect(u.ownStaticPath('http://my-host:3015/static/cover/x.jpg?v=2')).toBe(
      '/static/cover/x.jpg?v=2',
    )
  })

  it('脏输入不抛异常', async () => {
    const u = await loadUrls({})
    for (const bad of ['', '   ', 'not a url', 'http://', '://x']) {
      expect(() => u.ownStaticPath(bad)).not.toThrow()
      expect(u.isOwnBackendStaticUrl(bad)).toBe(false)
    }
  })
})

describe('resolveCoverUrl（X1：自有封面不能走 image_proxy）', () => {
  it('自有 /static/ 封面直连（走代理会 400）', async () => {
    const u = await loadUrls({})
    expect(u.resolveCoverUrl('/static/cover/x.jpg')).toBe('/static/cover/x.jpg')
  })

  it('第三方 CDN 封面走 image_proxy 代理', async () => {
    const u = await loadUrls({})
    const out = u.resolveCoverUrl('https://i0.hdslb.com/a.jpg')
    expect(out).toContain('/image_proxy?url=')
    expect(out).toContain(encodeURIComponent('https://i0.hdslb.com/a.jpg'))
  })

  it('空值返回空串', async () => {
    const u = await loadUrls({})
    expect(u.resolveCoverUrl(null)).toBe('')
    expect(u.resolveCoverUrl('')).toBe('')
  })
})

describe('isTauri', () => {
  it('有 __TAURI_INTERNALS__ 时判定为桌面端', async () => {
    const u = await loadUrls({ tauri: true })
    expect(u.isTauri()).toBe(true)
  })

  it('没有时判定为 Web', async () => {
    const u = await loadUrls({ tauri: false })
    expect(u.isTauri()).toBe(false)
  })
})
