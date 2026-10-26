/**
 * 封面地址解析。
 *
 * **实现已统一到 `lib/urls.ts`**，本文件只做薄转发，保留原有导出名，
 * 让 `VideoBanner` / `HistoryPanel` 等调用点无需改动。
 *
 * ## 为什么不再保留一份独立实现
 *
 * 施工时两个组各自写了一份「判断是否自己后端静态资源」的逻辑
 * （本文件与 `lib/urls.ts`）。两份等价实现并存会随时间漂移——一处改了判据、
 * 另一处没改，就会出现「历史列表封面正常、顶部 banner 破图」这类只在一处复现的
 * bug。所以合并为一处：**`lib/urls.ts` 是唯一实现**。
 *
 * 背景（X1）：`save_cover_to_static` 曾返回 `http://localhost:8483/static/cover/...`
 * 这类绝对地址，前端无条件把它塞进 `/api/image_proxy`，而后端 `assert_safe_url`
 * 先拦 loopback、再查 CDN 白名单（不含 localhost）⇒ 400。四个在线平台的封面恰在
 * 白名单内，所以**只有本地视频中招**。修法：后端改返回站内相对路径（治本），
 * 前端用 `resolveCoverUrl` 兜住存量绝对地址。
 */
export { ownStaticPath, isOwnBackendStaticUrl, resolveCoverUrl, staticUrl } from '@/lib/urls'
