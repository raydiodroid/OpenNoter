import { defineConfig } from 'vitest/config'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Vitest 配置（**独立于 `vite.config.ts`**）。
 *
 * ## 为什么不复用主配置
 *
 * `vite.config.ts` 里挂了 `unplugin-parcel-macros`（给 `@react-spectrum/s2`
 * 的 `style()` 宏用）。那套宏只在 Vite 的构建管道里生效，在测试环境里加载
 * 会报错；而且它会顺带把整个 S2 依赖树拉进来 —— 对一个只想测纯函数的测试
 * 套件来说完全是负担。
 *
 * 所以这里给一份最小配置：**只测零依赖模块**（`src/lib/urls.ts` 等）。
 *
 * ## 这也是 `src/lib/urls.ts` 存在的原因
 *
 * `lib/api.ts` 的依赖链是 `api.ts → toast.ts → @react-spectrum/s2`，
 * 后者的 parcel 宏在 node 里根本加载不了。所以 URL 纯函数被拆到
 * `lib/urls.ts`（零依赖），才能被这里的测试直接 import。
 *
 * 想测「用到 S2 组件的代码」需要另一套配置（jsdom + 处理宏），
 * 那是后续工作，不在 CLAUDE.md 承诺的范围里。
 */
export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.spec.ts'],
    environment: 'node',
    globals: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
