import fs from 'fs-extra'
import type { Manifest } from 'webextension-polyfill'
import type PkgType from '../package.json'
import { isDev, isFirefox, port, r } from '../scripts/utils'

/** 视频站 + 本机/已知 OpenNoter 前端与后端（Cookie 同步、API 调用、字幕预取）。 */
const VIDEO_HOST_PERMISSIONS = [
  '*://*.bilibili.com/*',
  '*://*.youtube.com/*',
  '*://youtu.be/*',
  '*://*.douyin.com/*',
  '*://*.iesdouyin.com/*',
  '*://*.kuaishou.com/*',
] as const

/**
 * 本机前端（3015）/ 后端（8483）任意端口。
 *
 * ## 为什么不写死某个公网域名
 *
 * 早期这里硬编码了一个 `https://opennoter.<某域名>/*`。两个问题：
 *   - 公开仓库里等于公布部署者自己的域名；
 *   - 别人部署到自己的域名后，扩展连不上（权限里没有那个域名）。
 *
 * 自定义公网部署改走 `optional_host_permissions`：用户在后端地址里填
 * 自己的域名后，扩展在需要时向浏览器申请一次该域名的权限（见下方
 * `optional_host_permissions`）。这样源码里不含任何具体部署信息，
 * 且对任何人的部署都可用。
 */
const OPENNOTER_HOST_PERMISSIONS = [
  'http://localhost/*',
  'http://127.0.0.1/*',
] as const

const CONTENT_SCRIPT_MATCHES = [
  ...VIDEO_HOST_PERMISSIONS,
  ...OPENNOTER_HOST_PERMISSIONS,
]

export async function getManifest() {
  const pkg = await fs.readJSON(r('package.json')) as typeof PkgType

  // update this file to update this manifest.json
  // can also be conditional based on your need
  const manifest: Manifest.WebExtensionManifest = {
    manifest_version: 3,
    name: pkg.displayName || pkg.name,
    version: pkg.version,
    description: pkg.description,
    action: {
      default_icon: 'assets/icon-512.png',
      default_popup: 'dist/popup/index.html',
    },
    options_ui: {
      page: 'dist/options/index.html',
      open_in_tab: true,
    },
    background: isFirefox
      ? {
          scripts: ['dist/background/index.mjs'],
          type: 'module',
        }
      : {
          service_worker: 'dist/background/index.mjs',
        },
    icons: {
      16: 'assets/icon-512.png',
      48: 'assets/icon-512.png',
      128: 'assets/icon-512.png',
    },
    permissions: [
      'tabs',
      'storage',
      'activeTab',
      'sidePanel',
      'contextMenus',
      'cookies',
    ],
    // 收窄：去掉 *://*/* / <all_urls>；保留视频站 + 本机/已知后端通信。
    // 自定义公网 backendUrl 需用户在浏览器「扩展详情」里额外授权，或改此清单。
    host_permissions: [
      ...VIDEO_HOST_PERMISSIONS,
      ...OPENNOTER_HOST_PERMISSIONS,
    ],
    // 自定义公网部署（如自建在 example.com）由用户在后端地址里填写，
    // 扩展在保存时用 browser.permissions.request() 申请该 origin。
    // 不写死具体域名：既避免公开部署者信息，也让任何人的部署都能用。
    optional_host_permissions: [
      'https://*/*',
      'http://*/*',
    ],
    content_scripts: [
      {
        matches: [...CONTENT_SCRIPT_MATCHES],
        js: [
          'dist/contentScripts/index.global.js',
        ],
      },
    ],
    web_accessible_resources: [
      {
        resources: ['dist/contentScripts/style.css'],
        matches: [...CONTENT_SCRIPT_MATCHES],
      },
    ],
    content_security_policy: {
      extension_pages: isDev
        // this is required on dev for Vite script to load
        ? `script-src \'self\' http://localhost:${port}; object-src \'self\'`
        : 'script-src \'self\'; object-src \'self\'',
    },
  }

  // add sidepanel
  if (isFirefox) {
    manifest.sidebar_action = {
      default_panel: 'dist/sidepanel/index.html',
    }
  }
  else {
    // the sidebar_action does not work for chromium based
    (manifest as any).side_panel = {
      default_path: 'dist/sidepanel/index.html',
    }
  }

  // FIXME: not work in MV3
  if (isDev && false) {
    // for content script, as browsers will cache them for each reload,
    // we use a background script to always inject the latest version
    // see src/background/contentScriptHMR.ts
    delete manifest.content_scripts
    manifest.permissions?.push('webNavigation')
  }

  return manifest
}
