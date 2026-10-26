#!/usr/bin/env node
/**
 * Tauri CSP 自检（F8）—— 不依赖 Rust 工具链，纯静态断言 + 交叉核对。
 *
 * ## 为什么需要这个脚本
 *
 * CSP 是「会挂应用」的那类配置：写错了 release 包能启动、但某个功能静默坏掉
 * （思维导图无样式、导出 PNG 失败、`invoke` 全挂），而且**本地没有桌面端构建
 * 环境时根本测不出来**。这个脚本把「CSP 必须包含哪些源」变成可执行断言，
 * 让每次改 CSP 都能立刻自证。
 *
 * 检查分三类：
 *   1. **必要性**：markmap 运行时真的会去请求的源（从 `transformer.getAssets()`
 *      和源码里的硬编码 URL 实测得出），漏了就是功能回归；
 *   2. **严格性**：`script-src` 不许出现 `'unsafe-inline'` / `'unsafe-eval'`
 *      —— 这是本补丁唯一真正的安全价值，为了兼容性放宽它等于白做；
 *   3. **一致性**：`csp` 与 `devCsp` 都不能是 `null`，且 dev 版要额外放行
 *      Vite dev server（否则 `cargo tauri dev` 起不来）。
 *
 * 用法：
 *   node scripts/check-tauri-csp.mjs          # 检查
 *   node scripts/check-tauri-csp.mjs --json   # 机器可读输出
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND = resolve(HERE, '..')
const CONF = resolve(FRONTEND, 'src-tauri/tauri.conf.json')

/** 解析 "a 'b' c" → { 'a': ["'b'", "c"] } */
function parseCsp(csp) {
  const out = {}
  for (const part of String(csp || '').split(';')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const [name, ...values] = trimmed.split(/\s+/)
    out[name.toLowerCase()] = values
  }
  return out
}

const errors = []
const warnings = []
function must(cond, msg) {
  if (!cond) errors.push(msg)
}
function should(cond, msg) {
  if (!cond) warnings.push(msg)
}

const conf = JSON.parse(readFileSync(CONF, 'utf8'))
const security = conf?.app?.security ?? {}
const prodCsp = security.csp
const devCsp = security.devCsp

// ---------------------------------------------------------------------------
// 1. 基本形态
// ---------------------------------------------------------------------------
must(prodCsp !== null && prodCsp !== undefined, "app.security.csp 是 null —— release 包完全没有 CSP")
must(typeof prodCsp === 'string' && prodCsp.trim().length > 0, "app.security.csp 为空字符串")
must(devCsp !== null && devCsp !== undefined, "app.security.devCsp 是 null —— `cargo tauri dev` 会回落到 csp，Vite HMR 被拦")

const prod = parseCsp(prodCsp)
const dev = parseCsp(devCsp)

// ---------------------------------------------------------------------------
// 2. 必要性：markmap / 应用运行时真的会请求的源
// ---------------------------------------------------------------------------
// 实测依据（node 跑 transformer.getAssets()）：
//   styles : https://cdn.jsdelivr.net/npm/katex@0.16.18/dist/katex.min.css
//            https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.11.1/styles/default.min.css
//   scripts: https://cdn.jsdelivr.net/npm/webfontloader@1.6.28/webfontloader.js
// 且 `markmap-common` 的 loadCSS 会对 CDN CSS 发 fetch() 确认可达 → connect-src 也要放行。
const CDN = 'https://cdn.jsdelivr.net'

must(prod['script-src']?.includes(CDN), `script-src 缺 ${CDN} —— markmap 运行时会往 <head> 塞 webfontloader.js，被拦后公式/代码高亮字体加载失败`)
must(prod['style-src']?.includes(CDN), `style-src 缺 ${CDN} —— KaTeX / highlight.js 的 CDN 样式表加载不了，思维导图无样式`)
must(prod['font-src']?.includes(CDN), `font-src 缺 ${CDN} —— KaTeX 字体来自 jsdelivr，缺了公式显示为方块`)
must(prod['connect-src']?.includes(CDN), `connect-src 缺 ${CDN} —— markmap-common 的 loadCSS 会对 CDN CSS 发 fetch() 探活，被拦后资源加载逻辑报错`)

// Adobe Typekit（S2 字体服务）
//
// `@react-spectrum/s2` 的 `<Provider>` 会无条件渲染 `<Fonts />`，它按 locale 注入
// `https://use.typekit.net/<id>.js`：zh-CN / zh-SG / zh-Hans → `qwy0qqd`，
// zh-HK → `wrf0dsa`，zh-Hant / zh → `twr1oiz`，ja → `pyb4hbv`，ko → `pux4zom`。
// 不精确匹配时回落到 `preloads`（en 的 Adobe 字体 URL）。
//
// 本项目 `<Provider locale="zh-CN">` 精确命中映射表 ⇒ **每次启动都会注入该脚本**。
// 不放行的话：中文界面字体加载失败（回落到系统字体，观感明显变差）+ 每次启动
// 产生一条 CSP 违规报告。
const TYPEKIT = 'https://use.typekit.net'
must(prod['script-src']?.includes(TYPEKIT), `script-src 缺 ${TYPEKIT} —— S2 <Provider> 会为 zh-CN 注入 typekit 脚本，被拦后中文字体失效`)
must(prod['font-src']?.includes(TYPEKIT), `font-src 缺 ${TYPEKIT} —— typekit 脚本本身再去加载字体文件，同样要放行`)
must(prod['connect-src']?.includes(TYPEKIT), `connect-src 缺 ${TYPEKIT} —— typekit 运行时会有 XHR/fetch`)

// Tauri 2 的 invoke 走 IPC，漏了它所有 invoke 全挂
must(prod['connect-src']?.includes('ipc:'), "connect-src 缺 ipc: —— Tauri 2 的 invoke 走 IPC，漏了它所有 invoke 全挂")
must(prod['connect-src']?.includes('http://ipc.localhost'), "connect-src 缺 http://ipc.localhost —— 同上")

// sidecar 后端（.env.tauri: VITE_API_BASE_URL=http://127.0.0.1:8483/api）
must(prod['connect-src']?.includes('http://127.0.0.1:8483'), "connect-src 缺 http://127.0.0.1:8483 —— 桌面端连不上 sidecar 后端（登录/任务列表全空）")
// 截图与封面（F9 令牌 URL 也是这个 origin）
must(prod['img-src']?.includes('http://127.0.0.1:8483'), "img-src 缺 http://127.0.0.1:8483 —— 截图/封面显示不出来")

// 导出：PNG 走 canvas.toBlob → blob:，SVG 兜底走 data:
must(prod['img-src']?.includes('blob:'), "img-src 缺 blob: —— 导出 PNG 会失败")
must(prod['img-src']?.includes('data:'), "img-src 缺 data: —— 二维码（QRCode.toDataURL）和 SVG 导出兜底会失败")

// markmap-view 在 SVG 里 append("style") 动态注入样式表 → style-src 必须有 unsafe-inline
must(prod['style-src']?.includes("'unsafe-inline'"), "style-src 缺 'unsafe-inline' —— markmap-view 会 svg.append('style')，样式被拦后导图不显示颜色/字号")

// ---------------------------------------------------------------------------
// 3. 严格性：script-src 绝不能放宽
// ---------------------------------------------------------------------------
must(!prod['script-src']?.includes("'unsafe-inline'"), "script-src 出现 'unsafe-inline' —— 这条是本次补丁的核心价值，绝不允许为兼容性放宽")
must(!prod['script-src']?.includes("'unsafe-eval'"), "script-src 出现 'unsafe-eval' —— 同上")
// 生产构建不需要 wasm（仓库无 .wasm 资源、无 WebAssembly 调用），能去掉就去掉
should(!prod['script-src']?.includes("'wasm-unsafe-eval'"), "script-src 含 'wasm-unsafe-eval' 但仓库里没有任何 .wasm —— 可以去掉以进一步收紧")

// 收口指令
for (const directive of ['object-src', 'base-uri']) {
  must(prod[directive]?.includes("'none'"), `缺 ${directive} 'none' —— 关闭历史遗留挂载点，防 <base> 劫持`)
}

// ---------------------------------------------------------------------------
// 4. devCsp：Vite dev server 必须放行，否则 cargo tauri dev 起不来
// ---------------------------------------------------------------------------
if (typeof devCsp === 'string' && devCsp) {
  const devConnect = dev['connect-src'] ?? []
  const devScript = dev['script-src'] ?? []
  must(devConnect.some((s) => s.includes('localhost:3015') || s.includes('127.0.0.1:3015')),
    "devCsp 的 connect-src 没放行 Vite dev server（localhost:3015）—— HMR 连不上")
  must(devConnect.some((s) => s.startsWith('ws://')),
    "devCsp 的 connect-src 没放行 ws:// —— Vite HMR 的 WebSocket 会被拦")
  must(devScript.includes("'unsafe-inline'") || devScript.some((s) => s.includes('3015')),
    "devCsp 的 script-src 既没 'unsafe-inline' 也没放行 dev server —— Vite 注入的 preamble 会被拦")
  must(devConnect.includes('ipc:'), "devCsp 的 connect-src 缺 ipc: —— dev 下 invoke 全挂")
}

// ---------------------------------------------------------------------------
// 5. devtools
// ---------------------------------------------------------------------------
const windows = conf?.app?.windows ?? []
for (const [i, w] of windows.entries()) {
  must(w.devtools !== true, `app.windows[${i}].devtools 是 true —— release 包会带调试入口`)
}

// ---------------------------------------------------------------------------
// 输出
// ---------------------------------------------------------------------------
if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ ok: errors.length === 0, errors, warnings }, null, 2))
} else {
  if (errors.length) {
    console.error('❌ CSP 自检失败：')
    for (const e of errors) console.error('   - ' + e)
  } else {
    console.log('✅ CSP 自检通过（生产 + 开发两套）')
  }
  if (warnings.length) {
    console.log('\n⚠️  提示：')
    for (const w of warnings) console.log('   - ' + w)
  }
}

process.exit(errors.length === 0 ? 0 : 1)
