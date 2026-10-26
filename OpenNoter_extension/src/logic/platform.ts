import type { Platform } from './types'

/**
 * 平台识别 —— **镜像** `backend/app/utils/url_parser.py` 的 `detect_platform()`。
 *
 * ## 为什么要重写（F12）
 *
 * 旧实现是子串启发式：
 *
 *     if (url.includes('douyin')) return 'douyin'
 *
 * 这正是后端**已经修掉**的那类问题（历史上 `?x=douyin` 能骗过校验，
 * 构成 SSRF）。扩展侧虽然只用于 UX 判断（真正的拦截在服务端），但两份不一致
 * 会持续制造「插件说支持、提交后后端拒绝」的错位。
 *
 * 现在严格镜像后端：
 *   - **只按 hostname 判断**，绝不看 path/query；
 *   - 域匹配用「完全相等 或 后缀 `.domain`」—— 避免 `evil-bilibili.com` 这类
 *     看起来像但实际不是的域名；
 *   - 覆盖短链域名（b23.tv / v.douyin.com …），旧实现认不出。
 *
 * ## 保持不变的部分
 *
 * 短链的**路径形态**无法在客户端判断（服务端也是「只看域名就放行」，
 * 因为跳转后的真实链接由下载器再校验）。所以这里对短链只要求域名匹配。
 *
 * ⚠️ 修改本文件时请同步 `backend/app/utils/url_parser.py::PLATFORM_HOSTS`。
 */

/** 与后端 `PLATFORM_HOSTS` 逐条对齐（顺序也一致，先匹配者优先）。 */
const PLATFORM_HOSTS: Array<[Platform, readonly string[]]> = [
  ['bilibili', ['bilibili.com', 'b23.tv', 'bili2233.cn', 'bilibili.tv', 'acg.tv']],
  ['youtube', ['youtube.com', 'youtu.be', 'youtube-nocookie.com']],
  ['douyin', ['douyin.com', 'iesdouyin.com', 'douyinpic.com']],
  ['kuaishou', ['kuaishou.com', 'chenzhongtech.com', 'gifshow.com', 'kwaicdn.com']],
]

/** 取 hostname，失败返回空串（与后端 `_hostname()` 同语义）。 */
function hostnameOf(url: string | undefined | null): string {
  if (!url) return ''
  try {
    // 补协议：用户可能粘贴 `www.bilibili.com/video/...` 这种没有 scheme 的
    const normalized = /^[a-z][a-z0-9+.-]*:\/\//i.test(url) ? url : `https://${url}`
    return new URL(normalized).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/**
 * 从链接识别平台；识别不出返回 null。
 *
 * 注意：返回 null **不代表后端一定不支持** —— 服务端还有更细的路径校验
 * （例如 `bilibili.com` 下的非视频页会被拒）。这里只用于「要不要显示平台图标 /
 * 预填平台选择」这类 UX 判断。
 */
export function detectPlatform(url: string | undefined | null): Platform | null {
  const host = hostnameOf(url)
  if (!host) return null

  for (const [platform, domains] of PLATFORM_HOSTS) {
    for (const domain of domains) {
      // 完全相等，或真正的子域（`.domain` 后缀）
      if (host === domain || host.endsWith(`.${domain}`)) return platform
    }
  }
  return null
}

export const PLATFORM_LABELS: Record<Platform, string> = {
  bilibili: '哔哩哔哩',
  youtube: 'YouTube',
  douyin: '抖音',
  kuaishou: '快手',
  local: '本地',
}
