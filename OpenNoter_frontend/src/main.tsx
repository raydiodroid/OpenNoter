import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@react-spectrum/s2/page.css'
import './styles/global.css'
import App from './App'
import { bootLoaderDone, setBootHint } from './lib/bootLoader'

// ---------------------------------------------------------------------------
// 启动骨架屏的退场时机
// ---------------------------------------------------------------------------
// `index.html` 里内联了一段启动骨架（在 JS 执行前就已渲染，避免白屏）。
// 这里负责在 React **真正画出内容之后**把它淡出。
//
// ## 为什么用双 requestAnimationFrame 而不是直接调
//
// `createRoot().render()` 是**异步**的：它把工作排进 React 调度器，此刻 DOM
// 里还没有任何内容。如果紧接着调 `bootLoaderDone()`，骨架会先消失、内容后出现
// —— 中间空出一帧白屏，比不骨架还差。
//
// 双 rAF 的语义是「等浏览器完成**这一帧的样式与布局**，再进入下一帧」，
// 此时 React 的首次提交已经落到 DOM 上。这是不引入 React 内部 API
// （如 `flushSync`）的最稳做法。
//
// ## 兜底
//
// 首帧渲染慢（低端机 + 大 bundle）时骨架会一直显示 —— 那正是想要的。
// 但有种坏情况：React 抛错 → 永远不渲染 → 骨架卡死。
// 下面加了 15s 上限，超时无论如何撤掉骨架，让用户至少能看到报错信息。
const BOOT_SAFETY_TIMEOUT_MS = 15_000

setBootHint('正在加载界面…')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

requestAnimationFrame(() => {
  requestAnimationFrame(() => {
    bootLoaderDone()
  })
})

window.setTimeout(() => {
  // 走到这里说明 15s 内 React 都没画出首屏 —— 骨架不该继续挡着
  bootLoaderDone()
}, BOOT_SAFETY_TIMEOUT_MS)
