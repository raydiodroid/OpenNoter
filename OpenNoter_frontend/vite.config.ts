import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import macros from 'unplugin-parcel-macros'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function readAppVersion() {
  const fallbackVersion = '0.0.0'

  try {
    const tauriConfigPath = path.resolve(__dirname, 'src-tauri/tauri.conf.json')
    const tauriConfig = JSON.parse(fs.readFileSync(tauriConfigPath, 'utf-8')) as { version?: string }
    return tauriConfig.version || fallbackVersion
  }
  catch {
    return fallbackVersion
  }
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // 在 Docker 环境中，父目录可能没有 .env 文件，使用当前目录
  const envDir = process.env.DOCKER_BUILD ? __dirname : path.resolve(__dirname, '../')
  const env = loadEnv(mode, envDir)

  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://127.0.0.1:8483'
  const port = parseInt(env.VITE_FRONTEND_PORT || '3015', 10)
  const appVersion = env.VITE_APP_VERSION || process.env.VITE_APP_VERSION || readAppVersion()

  return {
    // Web（nginx）从根路径提供资源，必须用绝对 base，否则刷新 /settings/ai 这类深链接会 404；
    // Tauri 里仍用相对路径。
    base: process.env.TAURI_ENV_PLATFORM ? './' : '/',
    define: {
      __APP_VERSION__: JSON.stringify(appVersion),
    },
    // @react-spectrum/s2 的 style() 宏在构建期展开成原子 CSS
    plugins: [macros.vite(), react()],
    // 宏在源码里以 import attribute 形式出现，Vite 的依赖扫描器（独立 esbuild 进程）
    // 不认识它，会直接让 dev server 起不来。关掉自动扫描，依赖按需转换即可。
    optimizeDeps: {
      noDiscovery: true,
      include: [
        'react',
        'react-dom',
        'react-dom/client',
        'react-router-dom',
        'axios',
        'zustand',
        'use-sync-external-store/shim/index.js',
        'use-sync-external-store/shim/with-selector.js',
        'style-to-js',
        'hoist-non-react-statics',
        'jszip',
        'pinyin-match',
        'fuse.js',
        'uuid',
        'idb-keyval',
        'react-markdown',
        'remark-gfm',
        'remark-math',
        'rehype-katex',
        'rehype-slug',
        'react-syntax-highlighter',
        'prismjs',
        'react-medium-image-zoom',
        'react-intersection-observer',
        'qrcode',
        'markmap-common',
        'markmap-lib',
        'markmap-view',
        'markmap-toolbar',
      ],
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            markdown: ['react-markdown', 'react-syntax-highlighter', 'remark-gfm', 'remark-math', 'rehype-katex'],
            markmap: ['markmap-lib', 'markmap-view', 'markmap-toolbar', 'markmap-common'],
            vendor: ['react', 'react-dom', 'react-router-dom'],
          },
        },
      },
    },
    server: {
      host: '0.0.0.0',
      port: port,
      allowedHosts: true, // 允许任意域名访问
      proxy: {
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
          rewrite: path => path.replace(/^\/api/, '/api'),
        },
        '/static': {
          target: apiBaseUrl,
          changeOrigin: true,
          rewrite: path => path.replace(/^\/static/, '/static'),
        },
      },
    },
  }
})
