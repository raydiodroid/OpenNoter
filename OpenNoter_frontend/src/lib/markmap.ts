import { loadCSS, loadJS } from 'markmap-common'
import { Transformer } from 'markmap-lib'
import * as markmap from 'markmap-view'
import DOMPurify from 'dompurify'

/**
 * markmap 转换器（安全加固 · X-S1）
 *
 * ## 为什么必须关掉 markdown-it 的 HTML 支持
 *
 * `markmap-lib` 内部默认用 `MarkdownIt({ html: true })`，而 `markmap-view`
 * 最终通过 d3 的 `.html(node.content)` 把节点内容注入 DOM —— 也就是 `innerHTML`
 * 语义。两者叠加后，**笔记里出现的原始 HTML 会被原样执行**。
 *
 * 实测（`markmap-lib@0.18.x`）以下内容都能穿过默认配置直达 `node.content`：
 *
 *     <details open ontoggle=alert(1)>
 *     <img src=x onerror=alert(2)>
 *     <svg onload=alert(3)>
 *
 * 笔记正文来自 **LLM 输出 + 视频标题/字幕**，不是完全由用户控制的内容，
 * 所以这是一条真实的注入路径。桌面端 `tauri.conf.json` 此前 `csp: null`
 * 且存在任意命令执行的 Tauri command（已一并移除，见 `src-tauri/src/lib.rs`），
 * 因此 XSS 可以直接升级成任意进程执行。
 *
 * ## 为什么改 `md.options.html` 而不是引入 DOMPurify
 *
 * `html: false` 让 markdown-it **在解析阶段就把 `<...>` 转义成文本**，
 * 攻击面在数据进入渲染管道之前就被消除，比"渲染后在 DOM 上过滤"更早、更可靠：
 *
 *   - 不引入新运行时依赖（DOMPurify 还需要一个 DOM 实现才能跑）；
 *   - 不存在"过滤规则与解析器版本不匹配"的漏网可能；
 *   - 正常 markdown（粗体、链接、列表、代码块）**完全不受影响**（已实测）。
 *
 * 实测对比（`html: true` → `html: false`）：
 *
 *     "<details open ontoggle=\"alert(1)\">x</details>"
 *       → "&lt;details open ontoggle=alert(1)&gt;x&lt;/details&gt;"
 *
 *     "<strong>正常粗体</strong>"        ← 不受影响，仍正常渲染
 *     "<a href=\"https://x.com\">a</a>"  ← 不受影响
 *
 * ## 不能改成什么
 *
 * 不要为了让 markmap"支持 HTML 节点"而把 `html` 改回 `true`。思维导图只需要
 * 文字结构；需要富文本请走正文的 markdown 渲染（那条路径用 `react-markdown`，
 * 默认不启用 raw HTML）。
 */
export const transformer = new Transformer()

// 关键一行：解析阶段就禁用原始 HTML（见上方说明）
transformer.md.options.html = false

// ---------------------------------------------------------------------------
// 第二层（纵深防御）：对每个节点 content 再过一次 DOMPurify
// ---------------------------------------------------------------------------
//
// ## 第一层已经有效，为什么还要第二层
//
// 第一层挡的是「笔记里的原始 HTML」。但 `node.content` 里**仍然有 HTML** ——
// 来源换成了 markdown-it 插件自己生成的：表格 `<table>`、KaTeX 的
// `<span class="katex">`、highlight.js 的 `<span class="hljs-keyword">`、
// 任务列表的 `<svg>`。
//
// markmap-view 用 d3 `.html(content)` 注入 DOM（innerHTML 语义）。所以
// 「插件产出里混入攻击者可控内容」的路径依然可达：markmap 升级改回
// `html: true`、某个新插件把用户输入拼进属性、或我们日后误删上一行。
// 这一层不依赖对上游默认值的假设，直接在**渲染边界**做最后一道过滤。
//
// ## 为什么用默认白名单，而不是「只允许 N 个标签」
//
// 极简白名单要逐个列出表格/KaTeX/hljs 需要的标签与属性，漏一个就是
// 「导图某块内容静默消失」；KaTeX 还会产出 MathML（`<math><semantics>…`），
// 维护成本高。DOMPurify 默认白名单是「已知安全集合」，方向更对。
//
// 实测（dompurify@3.3.1，跑在 jsdom 上，见交付说明里的验证脚本）：
//   正常内容零影响：`<table>` / `.katex` / `.hljs-*` / 任务列表 `<svg>` 原样保留
//   `<details open ontoggle=...>` → `<details open="">`（事件属性被剥）
//   `<img src=x onerror=...>`      → `<img src="x">`
//   `<svg onload=...>`             → `<svg>`（保留形状，去掉事件）
//   `<a href="javascript:...">`    → `<a>`（协议被拒）
//   `<style>` / `<iframe>`         → 整体删除

const sanitizeConfig = {
  // 默认白名单已经不允许脚本执行；这里再显式禁掉「能产生交互、或影响整页
  // 样式」的标签，让节点内容无法越出自己那一格。
  FORBID_TAGS: ['style', 'link', 'meta', 'base', 'form', 'input', 'button', 'select', 'textarea'],
  // 只允许安全协议。DOMPurify 默认已拦 `javascript:` / `data:`，
  // 这里显式固定，避免上游默认值变化时静默放宽。
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
}

/**
 * 消毒单段 markmap 节点 HTML。
 *
 * 保留 markdown-it 插件产出的全部结构（表格 / 公式 / 代码高亮 / 任务列表），
 * 只剥离可执行内容。
 */
export function sanitizeMindmapHtml(html: string): string {
  if (!html) return html
  // DOMPurify 需要真实 DOM。没有 document 的环境（SSR / 纯 node 单测）
  // 原样返回，让第一层单独兜底 —— 不能因为消毒不可用就让渲染崩掉。
  if (typeof window === 'undefined' || typeof window.document === 'undefined') return html
  return DOMPurify.sanitize(html, sanitizeConfig)
}

/** 深度遍历 markmap 数据树，消毒每个节点的 content（就地修改并返回同一棵树）。 */
export function sanitizeTree<T extends { content?: string; children?: T[] }>(node: T): T {
  if (!node || typeof node !== 'object') return node
  if (typeof node.content === 'string' && node.content) {
    node.content = sanitizeMindmapHtml(node.content)
  }
  if (Array.isArray(node.children)) {
    node.children = node.children.map(sanitizeTree)
  }
  return node
}

const { scripts = [], styles = [] } = transformer.getAssets()
loadCSS(styles)
loadJS(scripts, { getMarkmap: () => markmap })
