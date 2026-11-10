# OpenNoter 浏览器插件

在 Bilibili、YouTube、抖音、快手视频页提交笔记任务，连接自己的 OpenNoter 后端。插件使用 Vue 3 和 Manifest V3，包含 popup、side panel、options 和独立的后台 service worker。

## 已实现的功能

- 工具栏弹窗、视频页浮动入口、右键菜单提交任务。
- 侧边栏查看进度、Markdown、思维导图，复制与下载笔记，基于笔记进行 RAG 问答。
- B 站浏览器字幕预取；支持从当前浏览器同步四个平台的 Cookie。
- 自定义后端地址与 API Key；通过统一模型列表选择站点模型或个人 BYOK 模型，也可留空使用后端默认选择。
- 个人默认 / LLM / ASR 凭据设置。Cookie 根据站点策略写入个人接口或管理员共享接口。
- 管理员可管理站点供应商、转写配置和部署监控；普通用户只显示通用、个人凭据和下载设置。
- 任务缓存按后端地址与 API Key 的 SHA-256 摘要隔离：每个身份保留最近 30 条，总量最多 300 条。切换连接会停止旧轮询，拒绝旧请求回写。旧版未标注身份的缓存保留在本机，但不会自动显示给新身份。

插件并未整体过时。此前 README 把侧边栏、浮动入口、Cookie 同步、思维导图和问答误标为未来计划；本次已纠正，并更新任务 API、BYOK、权限与会话边界。

## 开发与加载

依赖：Node 20+、pnpm 9.7.1。

```bash
cd OpenNoter_extension
pnpm install --frozen-lockfile
pnpm dev
```

在 `chrome://extensions/` 开启开发者模式，加载 `OpenNoter_extension/extension/`。启动后端后，进入插件设置填写后端地址及当前账号创建的 API Key，再测试连接、刷新模型。自定义域名需要在「测试连通」时授予浏览器访问权限。

后端默认开启认证。生产 CORS 使用 `OPENNOTER_CORS_EXTENSION_IDS` 配置扩展 ID 白名单；开发版 ID 不固定时可在本地使用 `OPENNOTER_CORS_ALLOW_ANY_EXTENSION=true`。API Key 是登录后端用的凭据，与「我的模型凭据」里的上游模型 Key 不同。

## 验证与构建

```bash
pnpm typecheck
pnpm test --run
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e             # 先 build；测试加载打包产物并模拟后端响应
pnpm pack:zip
```

E2E 覆盖真实 popup 控件、普通用户的权限入口和修改连接时的焦点保持，已替换 Vitesse 模板断言。测试不调用真实模型、不发送平台 Cookie。Chrome/Chromium 是当前回归目标；存在 Firefox 打包命令并不代表已验证侧边栏等专有 API 的兼容性。

## 接口与实现边界

- 提交与查询使用 `POST /api/notes`、`GET /api/tasks/{id}`，统一消费 `{code, msg, data}`。
- `src/logic/client.ts` 接收显式连接快照，不依赖 Vue；popup 与后台 worker 共用此客户端。
- `src/logic/task-poller.ts` 管理单条轮询生命周期；401/403/404/409/410 停止轮询，切换任务使旧响应失效。
- 个人凭据只读取脱敏状态；修改必须提供完整新 Key。Cookie 同步不回读个人 Cookie 明文。
- 插件历史是本机最近任务缓存；完整跨设备历史由 Web 客户端和后端数据库提供。
- 动态自定义站点的网页 Cookie 桥接仍受 content-script 匹配范围限制。直接使用插件的「下载配置」同步 Cookie 不依赖网页桥接。

骨架基于 [vitesse-webext](https://github.com/antfu-collective/vitesse-webext)。
