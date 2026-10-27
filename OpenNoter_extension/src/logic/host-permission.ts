/**
 * 自定义后端域名的权限申请。
 *
 * ## 为什么需要运行时申请
 *
 * MV3 扩展能 fetch 哪些地址由 `host_permissions` 决定。内置的
 * `http://localhost/*` / `http://127.0.0.1/*` 覆盖了本机部署，但用户可能把
 * OpenNoter 部署到自己的公网域名 —— 那不在内置清单里。
 *
 * 早期实现是把某个具体域名写死进 `host_permissions`。问题：
 *   - 公开仓库等于公布部署者域名；
 *   - 别人部署到自己的域名后扩展直接连不上。
 *
 * 现在改成 `optional_host_permissions`：内置清单只留本机地址，公网域名
 * 由**用户在设置页填写时**现场申请。这样源码零部署信息，且任何人的部署
 * 都能用。
 *
 * ## 为什么必须在用户手势里调用
 *
 * `permissions.request()` 浏览器要求由用户操作触发（点击「保存」/「测试」），
 * 不能在后台自动弹窗。所以本模块只在用户主动操作时被调用。
 */

/** 本机地址不需要申请（已在内置 host_permissions 里）。 */
function isLocalOrigin(url: string): boolean {
  try {
    const u = new URL(url)
    return u.hostname === 'localhost'
      || u.hostname === '127.0.0.1'
      || u.hostname === '[::1]'
  }
  catch {
    return false
  }
}

/**
 * 把后端地址转成 host permission 模式（`origin/*`）。
 *
 * 返回 null 表示：地址无效，或本机地址（无需申请）。
 */
export function toHostPermissionPattern(backendUrl: string): string | null {
  const trimmed = (backendUrl || '').trim()
  if (!trimmed)
    return null
  try {
    const u = new URL(trimmed)
    if (!/^https?:$/.test(u.protocol))
      return null
    // 本机地址已在内置权限里，不必申请
    if (isLocalOrigin(trimmed))
      return null
    return `${u.origin}/*`
  }
  catch {
    return null
  }
}

/**
 * 为一个自定义后端地址申请可选权限。
 *
 * @returns `'granted'` 已授权 / `'denied'` 用户拒绝 / `'not-needed'` 本机或无效
 *
 * 注意：**必须在用户手势（点击事件）里调用**，否则浏览器会直接拒绝。
 */
export async function ensureHostPermission(
  backendUrl: string,
): Promise<'granted' | 'denied' | 'not-needed'> {
  const pattern = toHostPermissionPattern(backendUrl)
  if (!pattern)
    return 'not-needed'

  try {
    const already = await browser.permissions.contains({ origins: [pattern] })
    if (already)
      return 'granted'
    const granted = await browser.permissions.request({ origins: [pattern] })
    return granted ? 'granted' : 'denied'
  }
  catch {
    // 某些环境（Firefox 的某些版本、或非用户手势）会抛异常 —— 视为未授权，
    // 但不阻塞用户保存设置（后端地址照存，只是跨域请求可能被浏览器拦）。
    return 'denied'
  }
}
