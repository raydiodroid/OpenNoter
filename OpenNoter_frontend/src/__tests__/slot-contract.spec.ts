import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * S2 槽位契约的静态审计。
 *
 * ## 为什么需要这条测试（真实事故）
 *
 * 「设置 → 模型配置」页对管理员整块崩掉，ErrorBoundary 显示：
 *
 *     Error: A slot prop is required. Valid slot names are "description".
 *
 * 根因：`ByokSettings.tsx` 的 `<Radio>` 子节点里放了 S2 的 `<Text>`。
 * `Radio` 底层的 RAC `RadioField` 给 TextContext 挂的 slots **只有
 * `description`、没有 DEFAULT_SLOT**；而 S2 `Text` 会走
 * `useSlottedContext(TextContext, props.slot)`，当上下文有 slots 但传入的
 * slot 名（未传 = DEFAULT_SLOT）不在其中时，它**直接 throw**。
 *
 * 前端没有 jsdom（`vitest.config.ts` 刻意保持 node 环境，见那里的注释），
 * 所以这里做**源码级静态检查**：扫描受限组件子树里是否出现裸 `<Text>`。
 * 这不够"真"，但它能在零基建成本下把同一类错误挡住 —— 而这类错误的
 * 代价是整页崩掉。
 *
 * ## 为什么是这几个组件
 *
 * 判定依据是「该组件会给 S2 的 TextContext 挂 slots，且 slots 里没有
 * DEFAULT_SLOT」。逐个核对过 `node_modules/@react-spectrum/s2/src`：
 *
 *   - `Radio` / `Checkbox` / `Switch` / `Tag`：子节点直接暴露给用户，
 *     且各自的 TextContext.slots 只有 `description`（Tag 还有 errorMessage）。
 *   - 其余如 `Card` / `Menu` / `Picker` / `ComboBox` / `ListView` 等虽然也有
 *     slots，但它们**内部自己**会补 `slot="label"` 的 `<Text>`，用户代码拿到
 *     的位置通常已被重新覆盖；把它们列进来会误报。
 *
 * 注意 `Dialog` **不在**受限集合里：S2 的 `Dialog` 从未 Provider S2 的
 * TextContext（它只用 RAC 的 `RACTextContext.Consumer` 读值来给
 * `ContentContext` 传样式），所以 `Dialog > Content > <Text>` 不会抛错。
 */

const SRC = fileURLToPath(new URL('..', import.meta.url))

/**
 * 会提供「有 slots、无 DEFAULT_SLOT」的 S2 TextContext 的组件。
 * 在这些组件的 JSX 子树里不可以出现无 `slot` 属性的 `<Text>`。
 */
const SLOT_LIMITED_COMPONENTS = ['Radio', 'Checkbox', 'Switch', 'Tag'] as const

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue
      out.push(...walk(full))
    } else if (/\.tsx$/.test(name) && !/\.(test|spec)\.tsx$/.test(name)) {
      out.push(full)
    }
  }
  return out
}

/**
 * 找出 `openTag` 从 `from` 开始的那个元素，返回其子树文本与结束位置。
 * 用大括号 / 引号配平，能正确处理 `{}` 表达式里的 `>` 与嵌套同名标签。
 */
function readElementSubtree(
  src: string,
  from: number,
  tagName: string,
): { body: string; end: number } | null {
  let i = from
  let braceDepth = 0
  let quote: string | null = null

  // 先跨过开标签，找到它的 `>`
  for (; i < src.length; i++) {
    const c = src[i]
    if (quote) {
      if (c === quote && src[i - 1] !== '\\') quote = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue }
    if (c === '{') braceDepth++
    else if (c === '}') braceDepth--
    else if (c === '>' && braceDepth === 0) break
  }
  if (i >= src.length) return null
  // 自闭合 `<X />`：没有子树
  if (src[i - 1] === '/') return { body: '', end: i + 1 }

  const bodyStart = i + 1
  let depth = 1
  const tagRe = new RegExp(`<(/?)${tagName}\\b`, 'g')
  tagRe.lastIndex = bodyStart
  let m: RegExpExecArray | null
  while ((m = tagRe.exec(src))) {
    // 跳过自闭合的开标签
    if (m[1] === '') {
      let j = m.index
      let bd = 0
      let q: string | null = null
      for (; j < src.length; j++) {
        const c = src[j]
        if (q) { if (c === q && src[j - 1] !== '\\') q = null; continue }
        if (c === '"' || c === "'" || c === '`') { q = c; continue }
        if (c === '{') bd++
        else if (c === '}') bd--
        else if (c === '>' && bd === 0) break
      }
      if (src[j - 1] === '/') continue // `<X ... />`，不增加嵌套
      depth++
    } else {
      depth--
      if (depth === 0) return { body: src.slice(bodyStart, m.index), end: tagRe.lastIndex }
    }
  }
  return null
}

/**
 * 把注释替换成等长空白（保留换行，行号不变）。
 *
 * 必须去掉注释再扫：本次事故的修复本身就在注释里写了 `<Text>` 说明「不能用它」，
 * 不处理会把自己的文档判成违规（第一版测试就是这么误报的）。
 */
function stripComments(src: string): string {
  // 行注释：// ...
  let out = src.replace(/\/\/[^\n]*/g, m => ' '.repeat(m.length))
  // 块注释：/* ... */（跨行，保留其中的换行）
  out = out.replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, ' '))
  return out
}

function findViolations(rawSrc: string): number[] {
  const src = stripComments(rawSrc)
  const lines: number[] = []
  for (const tagName of SLOT_LIMITED_COMPONENTS) {
    const openRe = new RegExp(`<${tagName}\\b`, 'g')
    let m: RegExpExecArray | null
    while ((m = openRe.exec(src))) {
      const sub = readElementSubtree(src, m.index, tagName)
      if (!sub) continue
      // 子树里出现 `<Text` 且该 Text 自身没有 `slot=` 属性 → 违规
      const textRe = /<Text\b((?:[^>{}]|\{[^{}]*\})*)>/g
      let t: RegExpExecArray | null
      while ((t = textRe.exec(sub.body))) {
        if (!/\bslot\s*=/.test(t[1])) {
          // 行号：从子树起点往后找该 Text 的实际位置
          const idx = src.indexOf(t[0], m.index)
          lines.push((idx >= 0 ? src.slice(0, idx) : src).split('\n').length)
        }
      }
      // 从子树之后继续扫，避免重复匹配嵌套同名标签
      openRe.lastIndex = sub.end
    }
  }
  return lines
}

describe('S2 槽位契约：受限组件里不能放裸 <Text>', () => {
  const files = walk(SRC)

  it('至少扫描到源码文件（防止 walk 失效导致测试空转）', () => {
    expect(files.length).toBeGreaterThan(10)
  })

  it('Radio / Checkbox / Switch / Tag 子树内不得出现无 slot 的 <Text>', () => {
    const problems: string[] = []
    for (const file of files) {
      const src = readFileSync(file, 'utf8')
      const lines = findViolations(src)
      for (const line of lines) {
        problems.push(`${relative(SRC, file)}:${line}`)
      }
    }
    expect(problems).toEqual([])
  })

  /**
   * 事故原点的定点回归。
   *
   * 上面那条通用规则依赖「组件名匹配 + 子树扫描」；这条直接盯住出事的文件，
   * 防止通用规则因为重构（比如把 Radio 换成别的写法）而静默失效。
   */
  it('ByokSettings 的 BYOK 模式选项不得再用 S2 Text 作为 Radio 子节点', () => {
    const file = join(SRC, 'pages/settings/ByokSettings.tsx')
    const src = stripComments(readFileSync(file, 'utf8'))
    const radioIdx = src.indexOf('<Radio')
    expect(radioIdx).toBeGreaterThan(-1)

    const block = src.slice(radioIdx, src.indexOf('</RadioGroup>'))
    expect(block).not.toMatch(/<Text\b/)
    // 修好之后，标题/描述应该包在同一个容器里（否则网格会把标题挤到右边）
    expect(block).toMatch(/optionText/)
  })
})
