# OpenNoter 前端功能清单与设计基线

> 本文档是重写后的唯一功能/设计基线。所有页面均以 Adobe Spectrum 2（`@react-spectrum/s2`）为唯一设计语言，
> 已移除 Tailwind v4、shadcn/ui（Radix）、lucide-react、react-hot-toast 与 react-resizable-panels。
> 修改页面前先对齐这里的「结构规范」和「功能清单」，避免每个页面各自发明排版。
> 现状与 Spectrum 2 的逐项差异、修复记录与验证见 [`DESIGN-AUDIT.md`](./DESIGN-AUDIT.md)。

## 0. 技术底座

| 项 | 现状 |
| --- | --- |
| 框架 | React 19 + Vite 6 + TypeScript 5.7 |
| 设计系统 | `@react-spectrum/s2`（组件 + `style()` 宏 token），构建期由 `unplugin-parcel-macros` 展开 |
| 状态 | Zustand；任务历史持久化到 IndexedDB（`idb-keyval`），聊天记录持久化到 localStorage |
| 路由 | react-router-dom v7；Tauri 用 `HashRouter`，Web 用 `BrowserRouter` |
| 请求 | axios 实例（`src/lib/api.ts`）+ 统一解包 + 全局 toast（S2 `ToastQueue`） |
| 图标 | 全站 UI 图标只用 `@react-spectrum/s2/icons`（16/20/24 三档，含侧栏、思维导图缩放条）；品牌 logo 用自绘 SVG / `@lobehub/icons` 的 Mono 变体 |
| 字体 | 只用思源系：`Source Han Sans SC`（400/500/700）、`Source Han Serif SC`（数学公式）、`Source Han Mono SC`（代码）；字体文件在 `public/fonts/`，不列任何系统字体 |
| 主题 | 明暗双主题。S2 token 自身即 `light-dark()`，组件层无需覆盖；手写 CSS 必须用 `light-dark()` |

### 0.1 设计规约（硬规则）

1. **应用框架只有一个**：`src/components/AppShell.tsx`
   - 56px header：24px 品牌标识 + 全局搜索 + 明暗切换 + 账户菜单；header 与侧栏同用 `layer-1`，不画顶部卡片。
   - 侧栏几何**完全对齐官方 S2 `SideNavPanel` 示例**
     `styles={style({gridArea: 'sidebar', width: 208, marginX: 8})}`：
     展开 **208px**；左右各 8px 写在面板自身的 `marginX` 上（垂直不留白，面板通高，
     S2 `SideNavPanel` 自带 `height: full`）；折叠 **42px** 由 S2 内置的 `--collapsedWidth` 决定，不由我们控制。
     内部用 S2 `SideNav` + 两个 `SideNavSection` + `SideNavItemLink`。
   - 分组间距 **24px**（官方 token `side-navigation-item-to-header` / `container-gap-extra-large`）：
     S2 的 `SideNavSection` 自带 `marginTop: 24`，但宏的条件写法 `':not(:first-child)'` 不被支持、
     编译后并未生效，因此由 `global.css` 的 `.app-nav [role='rowgroup']` 手动补上。
   - 分组之间**不画分隔线**：app frame 规范里的那条分隔线指的是侧栏底部「end section」
     与状态控制按钮之间的分隔，本项目没有 end section。
   - 内容区 `base` 底 + 左上 `xl`（16px）圆角 + `shadow: emphasized`，贴住 header、视口右/下沿。
2. **页面骨架只有一个**：`src/components/Page.tsx`
   - `Page`：滚动容器，内容最大宽度 1200，左右/上下 32px，`boxSizing: border-box`。
   - `PageHeader`：全页唯一一级标题（`heading-lg` 28px），描述 `body` 16px，最多 720px；不包卡片。
   - `Section`：分组标题 `title` 16px/500，Sentence case；不放大写、不加宽字距。
   - `Panel`：`layer-1` 底 + 10px 圆角 + 24px 内边距。
     **不再提供「白底 + 1px 边框」变体** —— Spectrum `TableView` 自带 1px `gray-300` 边框与 6px 圆角，
     再外套一层描边容器会变成双层边框（详见规则 11）。
3. **圆角只用 S2 标度**：控件 8（`default`）、卡片/面板 10（`lg`）、弹层/登录卡 16（`xl`）、胶囊/头像 `full`；禁止 20px。
4. **所有颜色/间距/字号走 `style()` 的 token**，不写 Tailwind 类名；单边分隔线用 1px border 工具类或 `borderColor + 四条 border*Width`。
   间距只用 S2 的 0/2/4/8/12/16/24/32/40/48/64/80/96；布局表面统一 `base`，分组用 `layer-1`。
5. **表单控件宽度上限 560px**；表格/监控卡片可以铺满。页面级表单容器上限 720–760。
6. **行内提示统一用 `Notice`**（`src/components/Notice.tsx`）：1px 语义色边框 + 同色系 subtle 底 + 12/8 内边距；
   需要标题 + 操作时再用 S2 `InlineAlert`。`Notice` 自带 `role="status" / "alert"`。
7. **可切换条目统一用 `ListRow`**（`src/components/ListRow.tsx`）：供应商列表、下载平台列表、历史记录三处
   曾各写一套行容器/选中态/悬停态/焦点环，现统一为一套（行高 48、选中 `accent-100`、悬停 `.row-hover`、
   焦点 `.focus-ring`）；行主体是原生 `button` / `a`，不用 `div[role=button]`。
8. **胶囊标签统一用 `Chip`**（`src/components/Chip.tsx`）；只读的小标签用 S2 `Badge`。
9. **正文排版**在 `src/styles/global.css` 的 `.note-body`：16px / 1.7；h1 28 / h2 22 / h3 18 / 1.5；
   颜色用 Spectrum gray/accent 语义变量，明暗各一份。
10. **字体硬约束**：只允许思源系。S2 的 `:lang(zh-CN)` 内置字体规则用未分层 CSS 覆盖；代码/公式/markmap 分别用思源等宽/思源宋体/思源黑体。
11. **表格一律用 S2 `TableView`**，不再自绘 grid 表格，也不再外套描边容器。
    - 表格自带 1px `gray-300` 边框 + 6px 圆角 + `gray-25` 底；表头 `gray-75` 底 + 1px 下边框；单元格左右内边距 16px。
    - 全站统一：`density="spacious"`（48px 行）+ `overflowMode="truncate"` + `styles={style({width: 'full'})}`。
    - 加载态用官方 `loadingState="loading"`（表格内显示进度圈）、空态用 `TableBody renderEmptyState`，
      两者都渲染在表格内部，外层不再套 `Panel` 另画一块。
    - **空表要加 `.table-empty` 包裹**（见 `global.css`）：S2 的虚拟化表格在没有任何数据时会把表体
      压成 0 高度，内置空态因此被定位到可视区之外 —— 页面上根本看不到。给网格一个最小高度即可。
    - **第一列不要给固定宽度**：表格 `width: full` 时多出来的宽度会空挂在最右侧的图标后面。
      不给宽度的那一列会吸收多余空间（本项目统一是名称列），其余列按实际内容宽度给定。
    - 空态用 `EmptyState`（`components/Page.tsx`），传 S2 插画（`@react-spectrum/s2/illustrations/linear/*`），
      不要塞 20px 的图标 —— 插画槽位有固定高度，塞图标会缩成一个小点。
    - 行内操作按钮统一 `size="S"`（28px）+ `gap: 8` + `justifyContent: 'end'`：S2 的
      `ActionButtonGroup` 用的是 2/4/8px 标度，表格行内这一组 28px 的图标按钮配 8px
      才对得起（全站操作组也都是 8）。操作列宽度 = 按钮数 × 28 + 间距 + 32px 内边距。
    - 不做行选择时传 `selectionMode="none"`。
    - 需要「白底 + 描边」的容器时不要套 `Panel`，用 S2 组件自身的容器。
12. **状态用 `StatusIndicator`**（`src/components/StatusIndicator.tsx`）：圆圈里的 ✓ / ✗ / – + 文字。
    S2 的 `StatusLight` 只是一个实心圆点，表格里辨识度低；这里改用 S2 自带的
    `CheckmarkCircle` / `CloseCircle` / `RemoveCircle`，颜色用 `iconStyle({color})` 宏
    （内部写 `--iconPrimary`）走语义 token，明暗主题自动跟随。
    - ✓ `positive` — 完成 / 成功 / 可用
    - ✗ `negative` — 失败 / 不可用 / 已吊销
    - – `notice` / `neutral` — 进行中 / 未开始 / 未配置 / 不适用
13. **角色等分类标签用 `Badge`**，同一列的两种取值必须用**同一个组件 + 同一个 `fillStyle`**，
    只用 `variant` 区分强调程度（如管理员 `accent`、普通用户 `gray`，都用 `fillStyle="subtle"`），
    避免出现「只有一种取值有底色」的不一致。
9. **明暗双主题**：S2 的 `style()` token 本身就是 `light-dark()` 编译出来的（`base` → `light-dark(#fff,#111)`、
   `gray-200` → `light-dark(#e1e1e1,#323232)`），所以组件层不需要额外覆盖。
   - 主题状态在 `src/store/themeStore.ts`，写到 `html[data-theme='light'|'dark']`；
     `App.tsx` 的 `<Provider colorScheme={theme}>` 与之同步。
   - `html[data-theme='dark']` 同时改写 `color-scheme` 与 `--s2-color-scheme`，
     这样 `light-dark()` 与 S2 的 portal（弹层挂在 body 下）都能取到暗色值。
   - **全站手写 CSS 一律用 `light-dark(浅, 深)`**，不要写死单一色值；
     需要与 S2 同名 token 对齐时直接抄它的字面量（如 gray-100 = `#e9e9e9` / `#2c2c2c`）。
   - 顶层提供明暗切换按钮（`AppShell` header 右侧）。
   - 笔记正文的 `--note-*` 语义变量在 `:root` 与 `html[data-theme='dark']` 各给一份。

### 0.2 目录

```
src/
  App.tsx                 路由、认证/引导守卫、S2 Provider
  components/
    AppShell.tsx          Spectrum app frame（56px header + SideNavPanel 208/42 + 内容区）
    Page.tsx              Page / PageHeader / Section / Panel    ListRow.tsx           可切换条目的统一写法（供应商 / 下载平台 / 历史记录）
    Chip.tsx              胶囊标签（可带移除按钮）
    StatusIndicator.tsx   状态指示器（圆圈里的勾/叉/横线）
    Modal.tsx             轻量模态壳（启动/健康状态共用，含焦点循环）
    Notice.tsx            行内提示
    Brand.tsx             平台/供应商图标容器
    BackendInitDialog.tsx 后端启动中 / 启动失败
    BackendHealth/        Tauri 端后端进程状态与日志（仅桌面端）
    SystemDiagnostic/     启动诊断横幅（仅桌面端）
  pages/
    WorkbenchPage.tsx     工作台两栏（参数 380px + 预览）
    workbench/            表单、历史列表、预览、工具栏、Markdown、思维导图、转写、AI 问答
    settings/             7 个设置页（AI 模型 + 音频转写合并为 AISettingsPage）
    LoginPage / OnboardingPage / NotFoundPage
  lib/                    api、toast、format、markmap
  services/               后端接口封装（一域一文件）
  store/                  task / model / provider / auth / chat
  styles/
    global.css            视口、思源字体栈、`.note-body`、markmap 工具条
public/fonts/             思源黑体/宋体/等宽 woff2 + OFL 许可
```

## 1. 应用外壳与全局行为

- [x] 启动自检：轮询 `GET /sys_check`（2s 间隔，总超时 60s）；未就绪显示「后端初始化」对话框，
      超时显示失败面板（错误摘要 + 最近 6 行 stderr + 重启/重试 + 复制日志）。
- [x] 桌面端监听 `backend-ready` / `backend-startup-timeout` / `backend-terminated` 事件。
- [x] 认证初始化：`GET /auth/config` → 强制认证且未登录时跳 `/login`；`GET /auth/me` 恢复会话。
- [x] 全局任务轮询：每 3s 拉 `GET /task_status/{id}`，推进 PENDING→…→SUCCESS/FAILED；成功后落库多版本。
- [x] Spectrum app frame：56px header + 可折叠侧栏（工作台 + “设置”分组的 6 个二级菜单）+ 内容区；账户菜单位于 header 右上；未登录时显示“登录”按钮。
- [x] S2 `ToastQueue` + `ToastContainer`（右下角），失败 5s、成功 3s。
- [x] 路由级懒加载 + `ProgressCircle` 占位。

## 2. 工作台（`/`）

### 2.1 参数表单（左栏，可收起）

- [x] 平台：哔哩哔哩 / YouTube / 抖音 / 快手 / 本地视频（带品牌标）。
- [x] 视频链接；无协议头自动补 `https://`；必须是 http(s)。
- [x] 本地视频：文件路径 + 拖拽/点选上传（`POST /upload`），上传中/成功状态。
- [x] 模型（来自已启用模型）；无模型时给「去添加模型」入口。
- [x] 笔记风格（9 种）。
- [x] 视频理解开关；采样间隔 1–30s；拼图列/行各 1–10（默认 2×2）。
- [x] 输出内容多选：目录 / 原片跳转 / 原片截图 / AI 总结；`link` 在本地视频禁用、`screenshot` 未开视频理解时禁用。
- [x] 备注（extras）。
- [x] 提交：`POST /generate_note`；生成中禁用；已有当前任务时按钮变「重新生成」，旁边出现「新建笔记」。
- [x] 转写模型未就绪（`reason=transcriber_model_not_ready`）时提示并跳转音频转写页。
- [x] 选中已有任务时回填表单（编辑/重新生成复用参数）。

### 2.2 预览区（右栏）

- [x] 空闲：`IllustratedMessage` 引导。
- [x] 生成中：`ProgressCircle` + 步骤条（解析链接→下载音频→转写文字→总结内容→保存完成）。
- [x] 失败：错误插画 + 重试（复用 `formData` + `task_id`）。
- [x] 顶部工具栏：版本选择（多版本）、模型/风格徽标、创建时间；思维导图、复制、导出、原文参照、AI 问答。
- [x] 视频信息横幅：封面（`/image_proxy`）、标题、UP 主、平台、跳转原视频。
- [x] 笔记正文：GFM、KaTeX、标题锚点（精确 + 模糊匹配）、代码高亮 + 复制、原片时间胶囊、
      图片代理 + 点击放大、表格、引用、分割线。
- [x] 思维导图：markmap + 缩放工具栏；导出 XMind / SVG / PNG / 复制图片 / HTML / 全屏。
- [x] 原文参照：分段时间轴，点击高亮，底部片段数与总时长。
- [x] AI 问答：`POST /chat/index` 建索引（轮询 `GET /chat/status`）、`POST /chat/ask` 提问、引用来源可展开、
      清空会话、半屏/全屏切换。
- [x] 打开「原文参照」或「AI 问答」时自动收起参数面板，保证正文可读宽度。
- [x] 参数面板/预览区之间可拖拽 resize（320–560，默认 380，双击复位，宽度记忆）；侧栏按规范只固定/折叠，不默认 resize。

## 3. 历史（工作台左栏「历史」Tab）

- [x] 与「生成笔记」合并为同一页，左栏顶部用 S2 Tabs 切换；右栏始终是阅读器。
- [x] 列表：封面缩略图、标题（单行省略）、状态灯、创建时间、删除（AlertDialog 二次确认）。
- [x] 点击一行：设为当前任务并切回「生成笔记」，阅读器展示该笔记、表单回填参数。
- [x] 搜索：标题模糊（Fuse）+ 拼音（`pinyin-match`）。
- [x] 空态：无记录 / 无搜索结果两种插画文案。
- [x] 删除：本地移除 + `POST /delete_task`；旧 `/history` 链接重定向到 `/`。

## 4. 设置

### 4.1 模型与转写（`/settings/ai`，Tabs）

AI 模型与音频转写合并为「模型配置」一页，左侧一级菜单只保留一个入口；被关闭的供应商也直接展示，不再折叠。

#### Tab「AI 模型」（旧 `/settings/model` 重定向）

- [x] 左栏供应商列表：启用项常驻、禁用项折叠（计数）；隐藏 `groq` 转写槽位；整行可点。
- [x] 每行：探活（`POST /probe_endpoint`，只打 `GET /models`）+ 启用开关。
- [x] 新建/编辑：名称 / API Key / API 地址 / 类型（内置只读）；脏检查后保存；测试连通 `POST /connect_test`。
- [x] 模型：已启用模型 chip（可移除）；拉取远端模型列表 `GET /model_list/{pid}` + 搜索 + 勾选启用 `POST /models`。
- [x] 右侧空态：「选择一个供应商」。

#### Tab「音频转写」（旧 `/settings/transcriber` 深链接仍可用）

- [x] 引擎选择（`GET /transcriber_config` 的 `available_types`）。
- [x] 在线端点（groq 槽位）：端点 / API Key（已配置则留空不改）/ 模型名。
- [x] 本地 Whisper：模型大小下拉（已下载标记）、MLX 非 macOS 不可用提示。
- [x] 模型管理：下载状态（已下载/下载中/失败 + 错误）、下载/重试、下载中 3s 轮询、失败只提示一次。
- [x] 自定义模型（fast-whisper）：名称 + HF repo_id / 本地路径，列表 + 删除。
- [x] 保存配置；切到未下载模型时先确认。

### 4.3 下载配置（`/settings/download`）

- [x] 左栏平台列表（B 站 / YouTube / 抖音 / 快手）+ 全局代理配置。
- [x] 右栏 Cookie 编辑：`GET /get_downloader_cookie/{platform}`、`POST /update_downloader_cookie` + 获取说明。
- [x] 代理：`GET/POST /proxy_config`，含环境变量兜底提示。

### 4.4 密钥（`/settings/keys`）

- [x] 创建密钥 `POST /keys`，完整值仅显示一次 + 复制。
- [x] 表格：名称 / 前缀 / 最后使用 / 到期 / 状态 / 操作（编辑、轮换、用量、删除）。
- [x] 状态筛选用下拉框（`Picker`），不用一排按钮；删除即吊销，记录一并移除（`DELETE /keys/{id}?purge=true`）。

### 4.5 用户（`/settings/users`）

- [x] 表格：用户、身份来源（OIDC / 本地 / + 密码）、角色、状态、最后登录。
- [x] 角色列是安静型下拉框（`Picker` + `isQuiet`，表格里的密集控件用 quiet 变体），
      点角色标签直接改权限，选项为管理员 / 普通用户（`PATCH /auth/admin/users/{id}`）；
      选中值用 `renderValue` 渲染成角色 `Badge`。
- [x] **状态列本身就是一枚可点的 Badge**：绿底「启用」/ 灰底「停用」
      （`Badge variant='positive'|'neutral'` + `fillStyle="subtle"`），点一下切换
      （`PATCH /auth/admin/users/{id}`），不再单独占一个锁形图标按钮。
      `aria-label` 带用户名 + `aria-pressed`；`<button disabled>` + `.status-chip` 处理
      “不能停用自己”那一行。切换**不弹确认框**：乐观更新 + 可撤销 toast
      （`toast.successAction`），失败回滚；同一行的 PATCH 串行提交，连点不会乱序。
- [x] 角色列：管理员 `accent`、普通用户 `gray`（同一组件 / 同一 `fillStyle`，只差
      `variant`），无图标；两个取值固定 `width: 84` 且文字居中，3 字与 4 字左右缘对齐。
      下拉箭头用 `.role-picker` 从 DOM 层隐藏（S2 Picker 没有关闭 chevron 的开关），
      箭头位置的内边距一并归零。
- [x] 行内操作：只剩删除用户（`DELETE /auth/admin/users/{id}`，账号与 API 密钥一起清除，
      历史任务保留但解除归属，destructive 二次确认）。删除按钮常态即红色（S2 negative
      语义色 `#b72818` / 暗色 `#ff6756`），自己那一行为禁用灰。
      停用、删除、降级都不能对自己做。
- [x] 空表用 `.table-empty` 包裹，保证 S2 表格 0 高度时空态仍可见。

### 4.6 监控（`/settings/monitor`）

- [x] 30s 自动刷新 + 手动刷新，`GET /monitor/status`。
- [x] 五张卡：LLM 网关（供应商/延迟/模型数）、转写端点（名称/延迟/模型）、任务队列（进行中/成功/失败/累计 + 24h 失败）、
      磁盘（Meter + 已用/总量 + 产物占用）、FFmpeg（版本）。
- [x] 异常态：语义色 `negative` / `notice` + 错误文案。

### 4.7 关于（`/settings/about`）

- [x] 版本号、项目简介、特性网格、截图、快速开始（手动 / Docker 两个 Tab）、社区二维码、License。

## 5. 登录（`/login`）

- [x] 邮箱 + 密码 `POST /auth/login`，成功后回跳来源页。
- [x] OIDC 可用时提供「使用统一身份登录」（跳 `/api/auth/oidc/login`）。

## 6. 桌面端引导（`/onboarding`，仅 Tauri）

- [x] 4 步：后端连通性 → 供应商 + 模型（撞名复用并更新凭据）→ 转写引擎 → Cookie 说明。
- [x] 完成后写 `localStorage['opennoter-onboarded']`。

## 7. 404

- [x] 未匹配路由兜底 + 返回工作台。

## 8. 后端接口一览

```
POST /generate_note            GET  /task_status/{id}      POST /delete_task
POST /upload                   GET  /image_proxy?url=
GET  /sys_check                GET  /sys_health

GET  /get_all_providers        GET  /get_provider_by_id/{id}
POST /add_provider             POST /update_provider       POST /connect_test
POST /probe_endpoint           GET  /model_list            GET  /model_list/{pid}
POST /models                   GET  /models/delete/{id}    GET  /model_enable/{pid}

GET  /transcriber_config       POST /transcriber_config    GET  /transcriber_models_status
POST /transcriber_download     GET  /whisper_models        POST /whisper_models
DEL  /whisper_models/{name}    POST /update_asr_endpoint
GET  /get_downloader_cookie/{platform}   POST /update_downloader_cookie
GET  /proxy_config             POST /proxy_config          GET  /deploy_status

POST /chat/index               GET  /chat/status?task_id=   POST /chat/ask

GET  /auth/config              POST /auth/login             POST /auth/logout
GET  /auth/me                  POST /auth/me/password       GET  /auth/oidc/enabled
GET  /auth/oidc/login          GET  /auth/keys              POST /auth/keys
DEL  /auth/keys/{id}           GET  /auth/admin/users       PATCH /auth/admin/users/{id}

GET  /monitor/status
```

## 9. 本次重写修掉的缺陷

1. `useTaskPolling` 引用了不存在的 `updateTaskStatus`；`addPendingTask` 签名与实现不一致（永远存不到 `formData`）。
2. 轮询遇到一次网络抖动就把任务标成 `FAILED`；现在失败只由后端状态决定。
3. `TaskStatus` 只有 4 个值，后端返回的 `PARSING / DOWNLOADING / …` 全部被当成「进行中」；已补全。
4. 旧持久化数据里 `markdown` 是字符串；新增 `version: 2` 迁移，统一成版本数组。
5. `providerStore.loadProviderById` 只 `return` 不 `set`；`addNewProvider` 还在读已被拦截器解包的 `res.data.code`。
6. `ModelSelector` / `fetchModels` 仍按「未解包」的响应结构取 `res.data.data`。
7. `NoteForm` 无字段标签、分隔线硬编码、拼图默认值与校验范围不一致（3 vs 1–10）。
8. `MarkdownHeader` props 类型缺 `showTranscribe / viewMode`；线上实际靠隐式 any 运行。
9. 历史列表搜索框写了两遍、状态用裸色块、标题与状态抢宽度。
10. 全站混用 `bg-green-500 / text-emerald-700 / bg-amber-50` 等 Tailwind 调色板；已全部换成 S2 语义色。
11. Onboarding 是裸 HTML 控件 + 硬编码蓝色；已改为 S2 表单与单选。
12. `index.css` 里同时存在 Spectrum token、shadcn 变量、Tailwind theme 与两个垃圾备份文件；已删除。
13. 每个页面各自决定标题字号与内边距；已收敛到 `Page / PageHeader / Panel`。
14. `Dockerfile` 里的 `pnpm add -D @tailwindcss/typography` 临时补丁；已改为 `npm ci`。
15. `vite.config.ts` 的 `base: './'` 会让 `/settings/ai` 这类深链接刷新后去 `/settings/assets/*` 找 JS（404 + 白屏）；Web 构建改为 `/`，仅 Tauri 用相对路径。
16. `PickerItem` 里直接塞图标作为普通子节点，会落进 checkmark 栅格并和文字重叠；改为 `<Text slot="label">` 包住图标 + 文案。
17. 设置页内容宽度 960 在宽屏只占中间一条，下载配置左栏被挤到 205px；统一为 1200，并让下载页左栏固定 280、默认选中 B 站、Cookie 输入框保留多行高度。
