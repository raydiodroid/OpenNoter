/**
 * 启动骨架屏的生命周期控制。
 *
 * ## 背景
 *
 * `index.html` 里内联了一段启动骨架（品牌标识 + 进度条 + 内容骨架）。
 * 它在**任何 JS 执行之前**就已渲染，解决的是「bundle 下载/解析期间的纯白屏」。
 *
 * React 挂载完成后，这段骨架就该退场 —— 由本模块负责。
 *
 * ## 为什么单独一个文件
 *
 * 它需要在**多处**被调用（首帧渲染后、路由就绪后、异常兜底），而逻辑只有
 * 十几行。放在 `main.tsx` 里会让入口文件承担额外职责，也不便于单测。
 *
 * ## 时序
 *
 * ```
 * HTML 骨架可见
 *   → JS 下载/解析
 *   → React 首次提交（首屏内容已在 DOM）
 *   → done()：骨架 opacity → 0（200ms 过渡）
 *   → 移除骨架节点，body 恢复滚动
 * ```
 *
 * 关键点：**先让 React 的内容渲染出来，再淡出骨架**。反过来会出现
 * 「骨架没了但内容还没画」的一帧空白。
 */

const LOADER_ID = 'boot-loader'

/** 淡出过渡时长，必须与 `index.html` 里 `#boot-loader` 的 transition 一致 */
const FADE_MS = 200

/** 是否已经执行过（幂等：多次调用只生效一次） */
let done = false

/**
 * 隐藏并移除启动骨架。
 *
 * 幂等 —— 可以在任何地方放心调用多次。
 */
export function bootLoaderDone(): void {
  if (done) return
  done = true

  const el = typeof document !== 'undefined' ? document.getElementById(LOADER_ID) : null

  // body 的 overflow:hidden 是骨架屏为了防滚动加的，这里恢复
  if (typeof document !== 'undefined') {
    document.body.style.overflow = ''
  }

  if (!el) return

  el.classList.add('is-done')

  // 过渡结束后移除节点。用 setTimeout 而不是 transitionend：
  //   - transitionend 在元素被 display:none / 标签页不可见时可能不触发；
  //   - 这个节点的移除时机不敏感，晚 200ms 无所谓。
  window.setTimeout(() => {
    el.remove()
  }, FADE_MS)
}

/**
 * 更新骨架屏下方的提示文案。
 *
 * 用途：当加载进入某个明确阶段（比如「正在连接后端服务…」）时，
 * 让等待中的用户知道当前在做什么，而不是一直看「正在加载界面…」。
 *
 * 元素不存在时（已移除 / 非浏览器环境）静默忽略。
 */
export function setBootHint(text: string): void {
  if (done) return
  if (typeof document === 'undefined') return
  const hint = document.getElementById('boot-hint')
  if (hint) hint.textContent = text
}
