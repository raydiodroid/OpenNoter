# OpenNoter 前端设计审计：现状 vs Adobe Spectrum 2

> 审计对象：`OpenNoter_frontend` 当前工作区（React 19 + `@react-spectrum/s2` 1.8.2）。
> 设计参考：
> - [Spectrum · What's new](https://spectrum.adobe.com/getting-started/whats-new)
> - [App frame](https://spectrum.adobe.com/foundations/app-frame/overview)
> - [Side navigation](https://spectrum.adobe.com/foundations/app-frame/side-navigation-browsing-context)
> - [Containers](https://spectrum.adobe.com/foundations/layout-and-structure/containers)
> - [Rounding](https://spectrum.adobe.com/foundations/styles/object-styles/rounding)
> - [Typography system](https://spectrum.adobe.com/foundations/typography/typography-system)
> - [Background layers](https://spectrum.adobe.com/foundations/color/background-layers)
> - 以及 `@react-spectrum/s2` 包内的 `style()` token、SideNav / TableView / Dialog 实现。

## 0. 结论

上一版已经开始使用 S2 组件和 token，但**只替换了控件，没有替换应用框架和排版规则**。因此出现了“控件是 Spectrum、页面不是 Spectrum”的割裂：

- 顶栏只有品牌和用户菜单，侧栏却是另一张悬浮白卡，侧栏高度只到菜单底部；
- 内容区是一个 20px 圆角的白色大卡，卡片里再套白色卡片，层级靠边框而不是背景；
- 页面标题被包成 32px 内边距的 hero 卡，设置页标题字号 36px、关于页 51px；
- 20px 圆角不是 Spectrum token（large=10，extra-large=16，20 无对应项）；
- 侧栏用 lucide 图标，表格/按钮用 S2 图标，12/16/18/20/28px 混用；
- 字体同时存在系统栈、Adobe Clean Han、SimSun、Heiti SC、Source Code Pro、KaTeX 字体；
- 表格用自绘 grid 导致 1280 宽度下操作列被裁掉；按钮被塞进 `IllustratedMessage` 被拉成 100×100；
- `Page` 的 `width: full + padding` 没有 `border-box`，1280 宽度下设置页横向溢出。

本次按 Spectrum 2 的 app frame / type scale / container / rounding / background layers 重做，并统一到思源字体。

## 1. 应用框架

### 问题

| # | 现状 | 与 Spectrum 的差异 |
| --- | --- | --- |
| 1 | 顶栏飘着品牌，侧栏是独立白卡（`alignSelf: start`），高度只到菜单底部 | Spectrum app frame 是 header（必需）+ side navigation（可选）+ content area；侧栏属于框架，不是卡片 |
| 2 | 侧栏 240px、白底、20px 圆角、8px 外边距 | 侧栏应与外壳同底色、贴边、全高，不能悬浮 |
| 3 | 内容区 20px 圆角 + 1px 边框 | 内容区是 app frame 专用容器：base 底 + 左上 16px 圆角 + 轻微投影 |
| 4 | 顶栏与侧栏之间没有结构关系，品牌和导航分属两个视觉块 | header 放品牌与全局操作；side nav 只放一级导航 |
| 5 | 手写导航行：`<Link>` + 2px 指示条 + 加粗 | 直接用 S2 `SideNav` / `SideNavPanel`：32px 行高、选中 2×18 指示条、hover、焦点、折叠、tooltip 全由组件负责 |

### 修复

- `AppShell` 重写为 Spectrum app frame：
  - 56px header：24px 品牌标识 + 账户菜单（头像触发），无多余文字块；
  - `SideNavPanel`（展开 224px / 折叠 42px）+ `SideNav`；一级“工作台”和二级“设置”两个分区，用一条常驻分隔线区分，不使用会被折叠隐藏的 section header；
  - 内容区 `base` 底、`borderTopStartRadius: 16`、`boxShadow: emphasized`，贴住 header 下沿与视口右/下沿；
  - 侧栏底色 `layer-1`，与 header 同层，不再有白色悬浮卡。
- 导航图标直接使用 S2 `@react-spectrum/s2/icons` 组件（20px，视觉规格与全站一致）；侧栏的 `SideNavItemLink` 必须包在 `SideNavItemContent` 内，否则 React 19.2 下 SVG 会触发 `createElementNS` 异常。
- 侧栏折叠、键盘焦点、选中态、tooltip 交给 S2；折叠后内容区自动变宽。

## 2. 页面骨架与容器

### 问题

- `PageHeader` 是白色 20px 圆角、32px 内边距的卡片；每个设置页都像一张 hero 卡。
- `Page` 文档写“960px”，代码 `maxWidth: 960`；实际宽屏只占中间一条。
- `Panel` 文档写“1px gray-200 边框”，实现却只有白底、无边框 → 白卡片叠白卡片，分组不可见。
- 页面内边距 40 + 卡片内边距 32 + 标题 36px，纵向节奏过大；设置页首屏被标题吃掉。
- 圆角混用 20 / 16 / 10 / 8 / 6 / 12，且 20 不在 Spectrum 圆角系统里。

### 修复

- 统一 `Page`：
  - `maxWidth: 1200`，`paddingX: 32`，`paddingY: 32`，`boxSizing: border-box`（修 1280 横向溢出）；
  - 页头不再包卡片：h1 `heading-lg`（28px），描述 `body`（16px / CJK 1.7），与内容 32px 间距。
- 新增 `Section`：标题 `title`（16px / 500），描述 `detail`，内容默认 16px 节奏。
- `Panel` 两种形态：
  - `fill`（默认）：`layer-1` 浅灰底 + `lg`（10px）圆角 + 24px 内边距；
  - `outline`：`base` 白底 + 1px `gray-200` 边框 + 10px 圆角 + `overflow: hidden`，用于 TableView。
- 圆角收敛到 S2 标度：控件 8、卡片/面板 10、弹层/登录卡 16、头像/胶囊 full；不再出现 20px。

## 3. 排版

### 问题

- 页面标题 36px（`heading-xl`）且 `lineHeight: 1.2`，违反 S2 CJK 行高；关于页 51px；登录/引导 28px。
- 分组标题 14px + `text-transform: uppercase` + 0.06em 字距。中文没有大小写，这套规则等于无效；英文也不该在应用 UI 里全大写。
- 同一层级的“分组标题”在不同页面是 14 / 18 / 28px。
- 正文、说明、表单标签的字号和颜色混用 `body` / `ui` / `detail` / `body-sm`。
- 中文粗体曾被统一改成 700，导致标题过黑；S2 的 CJK bold 实际映射到 500，heading 才用 700。

### 修复（全站唯一标度）

| 角色 | 样式 | 说明 |
| --- | --- | --- |
| 页面标题 | `heading-lg` 28 / 700 / 1.5 | 不覆盖行高，交给 S2 CJK 规则 |
| 页面描述 | `body` 16 / 400 / 1.7 | 最大 720px |
| 分组标题 | `title` 16 / 500 | Sentence case，无大写/宽字距 |
| 卡片/面板标题 | `title` 16 / 500 | 与分组标题一致 |
| 正文/表单标签 | `ui` 14 / 400 | 控件、行文本 |
| 辅助说明 | `detail` 13 / 400 | `neutral-subdued` |
| 代码/等宽 | `code-sm` 12 + 思源等宽 | 表格前缀、时间戳、日志 |
| 笔记正文 | 16 / 1.7；h1 28、h2 22、h3 18 / 700 / 1.5 | Markdown 不在 S2 组件覆盖内，单独建标度 |

- 移除所有 `lineHeight: 1.2` / `1.35` 覆盖，中文标题用 S2 的 CJK 150%，正文 170%。
- 表单分组标题从 14 提到 16，避免与 14px 字段标签同级。
- 数字列/时间戳统一 `font-variant-numeric: tabular-nums`。

## 4. 字体（本次硬约束：只用思源系）

### 问题

- `global.css` 字体栈含 `-apple-system`、`PingFang SC`、`Segoe UI`、`Microsoft YaHei`、`Noto Sans SC`、`Georgia`、`Source Serif 4`、`Source Code Pro`、`ui-monospace`、`Menlo`。
- `@react-spectrum/s2` 的 `:lang(zh-CN)` 规则会强制切到 `adobe-clean-han-simplified-c, source-han-simplified-c, SimSun, Heiti SC Light`，覆盖 `--s2-font-family-sans`。
- Markdown 代码块被 `react-syntax-highlighter` 注入 `Fira Code, Fira Mono, Menlo, Consolas, ...` 内联样式。
- markmap 导出用 `Arial, Microsoft YaHei`；markmap 运行时文字、KaTeX 公式分别使用 markmap / KaTeX 自带字体。
- 实际渲染字体在 macOS 上是 PingFang / SimSun 兜底，不是思源。

### 修复

- 在 `public/fonts/` 内置思源字体（Noto CJK 是思源的 Google 发行版，字形同源）：

| 文件 | 字重 | 体积 | 用途 |
| --- | --- | --- | --- |
| `SourceHanSansSC-Regular.woff2` | 400 | 1.9 MB | 正文/控件 |
| `SourceHanSansSC-Medium.woff2` | 500 | 2.5 MB | S2 CJK bold（导航选中、分组标题、按钮） |
| `SourceHanSansSC-Bold.woff2` | 700 | 2.0 MB | S2 heading / 笔记标题 / strong |
| `SourceHanSerifSC-Regular.woff2` | 400–700 | 3.3 MB | 数学公式（按需加载） |
| `SourceHanMonoSC-Regular.woff2` | 400 | 2.4 MB | 代码、日志、时间戳（按需加载） |

  子集覆盖 7916 个常用汉字 + 拉丁 + 标点；字体许可见 `public/fonts/LICENSE-NotoCJK.txt`。
- `:root` 只保留思源栈；用**未分层**规则覆盖 S2 的 `@layer` i18n 字体：

```css
:lang(zh-Hans), :lang(zh-CN), :lang(zh-SG), :lang(zh) {
  font-family: var(--font-sans);
}
```

- 再加通配未分层规则，把 S2 portal / 隐藏 live region 也拉回思源；`code/pre/.u-mono` 用更高优先级切到思源等宽。
- 移除 SyntaxHighlighter 的 Fira 内联字体，显式改为 `var(--font-mono)`。
- markmap 运行时文字、导出 SVG/HTML、代码节点全部用思源；KaTeX 用 `!important` 覆盖成思源宋体，KaTeX 自带字体不再加载。
- 校验：`document.querySelectorAll('*')` 的 computed `font-family` 中不再出现 Adobe Clean、Fira、Menlo、Times、PingFang 等；`document.fonts` 实际加载的只有 Source Han Sans / Serif / Mono。

> 说明：S2 包自带的 `@font-face` / i18n 声明仍在构建产物 CSS 里（依赖包内部），但已被未分层规则覆盖，不参与渲染。若后续把 S2 升级到移除这些声明的版本，可删掉通配兜底。

## 5. 工作台

### 问题

- 页面外层 20px 圆角 + 边框，预览区里再套 20px 圆角空态卡；两层白卡。
- 左栏 420px，挤占阅读宽度；表单分组标题 14px 大写。
- 工具栏同时放版本、模型、风格、时间、5 个操作；1440 且参数面板展开时换行成两排，工具栏高度失控。
- 空态/失败态把 `Button` 放进 `IllustratedMessage`，按钮被拉伸成 100×100。
- 原文参照/AI 问答是“卡片里再放卡片”，带 12px 外边距，和 app frame 的垂直分栏不一致。
- 历史行用 `role=button` 包着删除按钮（嵌套交互元素）；选中行没有选中态。
- 代码块字体被 SyntaxHighlighter 覆盖成 Fira；图片 Zoom 渲染 `<div>` 到 `<p>` 里触发 React 警告。
- 思维导图导出工具条是浮动大胶囊，遮挡画布；markmap 默认彩虹色/第三方品牌标；右下角缩放条还是 markmap 自带图标。

### 修复

- 工作台不再有页面级卡片；左栏 380px `layer-1` + 1px 分隔线，右栏 `base`。
- 左栏表单：16px 内边距、24px 分组节奏、16px 分组标题；提交条 sticky 贴底。
- 工具栏只保留“版本选择 + 操作”，模型/风格/时间移到视频信息横幅的 meta 行；单行 48px。
- 空态/失败态去掉卡片与 `IllustratedMessage` 内按钮；步骤提示改成一行 detail 文案。
- 原文参照/问答改为贴边分栏：`border-start` + 48px header，不再有外圈卡片和外边距。
- 历史行改为“行容器 + 原生 button + 删除按钮兄弟节点”；选中行 `accent-100`，悬停/焦点态补齐。
- 代码块显式思源等宽；Zoom 改 `wrapElement="span"`，消除 `<p><div>`。
- 思维导图：导出工具条改为静态 48px 行；分支色收在 Spectrum 蓝/紫/绿/棕/洋红；markmap 品牌标隐藏，缩放条按 S2 工具条重绘。

## 6. 设置页

| 页面 | 主要问题 | 修复 |
| --- | --- | --- |
| 模型配置 | 供应商列表 320 + 全宽 Accent “添加供应商”；详情区 560 表单 + 白卡嵌套；`类型` 用只读输入框 | 280 列表 + 详情；二级按钮；`LabeledValue` 展示类型；Section + fill panel；供应商 logo 小写映射 |
| 转写 | 三级嵌套 Panel、SectionLabel 在外、表单 560 撑在满宽面板里、模型行自带卡片边框 | Section + 760 面板 + 560 表单；模型列表改为 outline 面板 + 分隔行；状态灯去掉内嵌图标 |
| 下载配置 | 左栏平台 + 代理挤在 320；“保存代理配置”全宽；Cookie 文本域只有一行 | 平台 280 + Cookie 1fr；代理独立 Section（720）；Cookie 文本域 132px 高；代理地址补可见 label |
| 密钥 | SectionLabel 弱、表格圆角未裁切、前缀用内联 12px 硬编码 | Section 16px；outline Panel + TableView；前缀用 `code-sm` |
| 用户 | 自绘 6 列 grid，1440 下操作列被裁掉；角色徽标是实心蓝；状态用彩色文字 | 改用 TableView（spacious）；角色 Badge neutral；StatusLight；操作按钮 16/20px |
| 监控 | 白卡叠白卡，指标卡不可见；标题 18px 偏大 | Section 卡片 `layer-1`、24px padding；卡片标题 16px；数值 tabular |
| 关于 | 51px hero + 28px 分组标题 + 16px 正文，和全站不同标度 | PageHeader + 紧凑 hero 面板；分组 18px；特性卡 `layer-1`、10px 圆角；Tabs 放 Panel 内 |

## 7. 图标与品牌标

> 来源与许可见 [`THIRD-PARTY.md`](./THIRD-PARTY.md)。UI 图标是 Adobe `@react-spectrum/s2/icons`（Apache-2.0）；平台/供应商品牌标不属于 Adobe。

- 移除 `lucide-react` 使用：侧栏、表格操作、设置页、聊天、工具栏全部使用 S2 图标组件（`@react-spectrum/s2/icons`）。
- 尺寸收敛到 16 / 20 / 24 三档；删掉 12px 图标；品牌标 20。
- `AILogo` 的映射键改为小写，DeepSeek / OpenAI / Qwen / Zhipu / Ollama 等不再全部落到默认 AI 图；未知供应商保留中性方框。
- 品牌方框 32×32、10px 圆角、`gray-200` 边框、白底，与 20px S2 图标对齐。

## 8. 可访问性与交互缺陷

1. **嵌套交互元素**：历史行 / 供应商行曾用 `div[role=button]` 包 `ActionButton` / `Switch`。改为外层容器 + 原生 button + 兄弟控件。
2. **按钮被拉伸**：`IllustratedMessage` 的 grid 会拉伸子按钮；空态/失败态按钮移出。
3. **自定义可点击行无焦点态**：统一 `focus-ring`；History/Provider/Platform 行都有可见焦点环。
4. **行内提示语义**：`Notice` 补 `role="status"` / `role="alert"`。
5. **启动对话框重复挂载**：S2 `DialogContainer` 在 React 19.2 下把内容嵌套重复渲染；改为共享 `Modal`（backdrop + elevated 卡片，Escape/遮罩可关、初始焦点），启动/健康状态共用。
6. **加载/空态**：`ProgressCircle` 有 aria-label；空态 Heading/Content 由 S2 组件提供语义。
7. **表格**：用户页从自绘 grid 改为 `TableView`，获得行/列语义、键盘导航和 `aria-label`。

## 9. 响应式与尺寸

- `Page` 修正 `border-box`，1280px 下设置页不再横向溢出。
- 工作台工具栏在 1280（参数面板展开）下保持单行；1200 以下允许换行，但不再出现“左组一行、操作另一行”的意外错位。
- 参数面板和预览区之间加入可拖拽分隔条：默认 380，范围 320–560，双击/键盘可复位或微调，宽度写入 localStorage；侧栏本身按 app frame 规范只做固定/折叠，不默认开启 resize（规范仅在侧栏含用户生成内容时建议 `isResizable`）。
- 视频横幅在窄宽度下自然堆叠（封面在上、信息在下），`原视频` 链接进入 meta 行，不再被 flex 换行甩到左侧。
- 侧栏可折叠到 42px；内容区自动扩展（S2 SideNavPanel）。
- 未实现 `<768px` 的移动端 overlay 导航；本项目当前按桌面优先，后续需要可接 S2 的移动端 overlay 方案。

## 10. 验证记录

- `npx tsc -b`：通过。
- `npx eslint src`：通过。
- `npm run build`：通过（仅剩既有 chunk > 500KB 警告）。
- 逐页 1440×960 / 1280×800 截图核对：工作台（空/失败/生成中/笔记/历史/原文参照/问答/思维导图）、模型、转写、下载、密钥、用户、监控、关于、登录、引导、404、后端初始化。
- 字体实测：可见元素的 computed `font-family` 全部为 Source Han Sans / Serif / Mono；KaTeX 字体不再加载。

## 11. 已知限制

1. 字体子集覆盖 7916 个常用字；极生僻字会回退到通用 `sans-serif`。若要求全字库，需要用完整 Source Han 字体替换（体积会显著上升）。
2. 关于页公众号/服务二维码仍是 GitHub raw 外链，离线环境会显示破图；建议后续下载到 `public/`。
3. `lucide-react` / `react-intersection-observer` 已在源码中无引用，但依赖仍在 `package.json`，清理需要同步更新 lockfile。
4. 构建产物仍有 `@react-spectrum/s2` 自带的 Adobe Clean / SimSun 字体声明；它们已被全局未分层规则覆盖，不参与渲染。
5. 侧栏 S2 `SideNav` 是 alpha 组件；无子项的隐藏展开按钮仍会生成 `aria-label="扩展"` 的 DOM 节点（`visibility: hidden`），不影响键盘与读屏。

## 12. 2026-10-20 反馈修正

针对“折叠图标错位 / 右上角菜单 / 区域边距 / 图标来源”四点补充：

1. **折叠侧栏**
   - 去掉侧栏 nav 外层的 8px 水平 padding。S2 `SideNavPanel` 自己用 12px 层级缩进把 21px 图标放进 42px 轨道，外层再加 padding 会让图标在展开/收起时横向跳 8px。
   - 侧栏改用原生 S2 图标组件（不再用 CSS mask）。图标在展开/收起两态都应用相同的 `inset-inline-start: -1.5px`，中心与 42px 轨道严格对齐，动画过程中 x 不变（实测始终为 10.5）。
   - 收起/展开时图标的**纵向**位置会随“设置”标题的隐藏/出现而重排，这是 S2 `SideNavPanel` 的原生内容折叠行为，不是图标错位；横向漂移已消除。
   - 隐藏的“设置”section header 不再留下 24px 空档：折叠时 section 间距改为 8px + 1px 分隔线。
   - 底部折叠按钮在折叠状态下去掉 `marginStart: 4` 并居中。
2. **右上角账户菜单**
   - 触发按钮保持 S2 `ActionButton isQuiet` + 24px 头像，符合 header universal nav 的安静图标按钮形态。
   - 菜单改为两个 `MenuSection`：第一段是用户身份（姓名 / 邮箱 / 角色），第二段是“退出登录”，中间由 S2 MenuSection 分隔线分开。菜单宽度 220，右对齐到 header 的 16px 内边距。
3. **区域边距**
   - 页面内容最大宽度从 1120 调整回文档基线 **1200**，1440 宽屏下两侧不再空出 80px；仍保留 32px 内边距。
   - 清理最后几个 20px 特例（工作台表单顶部、转写自定义模型、关于页二维码/页脚、监控页脚），统一到 24px 节奏；布局层级固定为：页面区域 32 / section 标题到内容 12 / 控件组 16 / panel 内边距 24 / 标题到描述 8。
4. **未登录态**
   - header 右上角在无用户时显示 S2 `Button variant="secondary"` 的“登录”按钮，点击去 `/login`。
   - 强制认证模式下仍由 `AuthGuard` 直接重定向；按钮只覆盖“可选认证 / 会话失效”的边界态。
5. **图标来源**
   - UI 图标全部来自 Adobe `@react-spectrum/s2/icons`（Apache-2.0），侧栏也直接使用原生图标组件，没有再用 CSS mask 绕行。
   - 平台/供应商品牌标不是 Adobe 图标：平台标是项目内 SVG，供应商标是 `@lobehub/icons`（MIT）+ 项目兜底图。
   - 许可与归属记录在 [`THIRD-PARTY.md`](./THIRD-PARTY.md)。


## 13. 2026-10-20 第二轮反馈修正

1. **侧栏不再纵向重排**
   - 去掉会被折叠隐藏的 `SideNavHeader`，改为在两个 `SideNavSection` 之间放一条常驻分隔线（8px + 1px），展开/收起都保留。
   - 实测 42px/224px 两态以及 200ms 动画过程中，图标 `x=12`、`y` 都不变；不再依赖隐藏 section header 后的重新排版。
   - 分隔线在 `layer-1` 上全宽，侧栏背景继续从视口左缘 bleed 到状态控制按钮；导航内容使用 S2 的 12px level inset，没有额外卡片或外边距。
2. **侧栏图标更换**
   - 模型配置：`Settings` → `DataSettings`；密钥管理：`Key` → `Lock`；部署监控：`ChartTrend` → `CloudStateOnline`。
   - 保留 `Home` / `Download` / `UserGroup` / `InfoCircle`；全部仍是原生 `@react-spectrum/s2/icons` 组件，20px。
3. **工作台 split 可拖拽**
   - 新增 `role="separator"` 分隔条：指针拖动、方向键/Shift+方向键、Home/End、双击复位；默认 380，范围 320–560，按窗口宽度收敛，宽度持久化到 `localStorage`。
   - 分隔线 1px 常驻，hover/拖动/键盘聚焦变 2px 强调色；中间有 4×36 的可见 grip。
4. **内容页图标统一 S2**
   - 思维导图右下缩放条从 markmap-toolbar 换成 S2 `ZoomOut` / `ZoomIn` / `ZoomFitToScreen` / `Layers` + `ActionButton` / `ToggleButton`。
   - 全站 UI 图标现在都来自 `@react-spectrum/s2/icons`；仅平台标、AI 供应商品牌标和 OpenNoter 自有 logo 例外。

## 14. 2026-10-20 第三轮修正：明暗双主题 + 抽离共享件

### 14.1 先纠正一条错误结论

上一轮审计里写「S2 token 在暗色下失效（`gray-200`、`base` 静态不变）」，**这是错的**。查 `@react-spectrum/s2` 的
`style/spectrum-theme.ts` 与构建产物后确认：`style()` 宏把所有颜色 token 编译成 `light-dark(浅, 深)`：

| token | 编译结果 |
| --- | --- |
| `base` | `light-dark(#fff, #111)` |
| `layer-1` | `light-dark(#f8f8f8, #1b1b1b)` |
| `layer-2` | `light-dark(#eee, #222)` |
| `gray-100` | `light-dark(#e9e9e9, #2c2c2c)` |
| `gray-200` | `light-dark(#e1e1e1, #323232)` |
| `accent-100` | `light-dark(#e5f0fe, #0c2175)` |
| `neutral` | `light-dark(#292929, #dbdbdb)` |

S2 的 `Provider` 负责把 `color-scheme: var(--s2-color-scheme)` 挂到自己的容器上，`<Provider colorScheme>` 一变，
全部 token 自动跟着切。**组件层不需要为暗色写任何覆盖。**

### 14.2 真正的暗色破口与修复

破口只在「手写 CSS」这一层，全部是写死的浅色值：

| 位置 | 修复前 | 修复后 |
| --- | --- | --- |
| `global.css` `.row-hover:hover` | `#f5f5f5` | `light-dark(#e9e9e9, #2c2c2c)`（对齐 S2 gray-100） |
| `global.css` `.focus-ring:focus-visible` | `#3b63fb` | `light-dark(#3b63fb, #5681ff)`（对齐 S2 focus-indicator） |
| `global.css` `.panel-resizer::before/::after` | `#3b63fb` / `#b3b3b3` | 同上 |
| `global.css` `.note-body blockquote` 引用线 | `#cacaca` | `light-dark(#cacaca, #4a4a4a)` |
| `MarkdownArticle` 代码高亮 | 固定 `oneLight` | 按 `themeStore` 切 `oneLight` / `oneDark` |
| `MarkdownArticle` `.note-body pre` | 内外各画一次边框+圆角 | 外框交给 `codeBlock` 容器，`pre` 只定排版 |

另外把 `FRONTEND-INVENTORY.md` §0.1 规则 9 从「**只做浅色**」改成「明暗双主题」——
原规则与既有代码（`themeStore` + `<Provider colorScheme>` + 顶栏切换按钮）本来就矛盾。

### 14.3 抽离共享件

| 新件 | 收拢了什么 |
| --- | --- |
| `components/ListRow.tsx` | 供应商行（ModelPage）、下载平台行（DownloaderPage）、历史行（HistoryPanel）三份复制实现 → 一套；行高 48、选中 `accent-100`、悬停 `.row-hover`、焦点 `.focus-ring` 统一；行主体是原生 `button`/`a` |
| `components/Chip.tsx` | ModelPage 的可移除胶囊 → 命名组件；MonitorPage 的自绘 tag 换成 S2 `Badge` |
| `AboutPage` 卡片 | 自绘 `card` / `qrCard` 两种卡片 → 统一用 `Panel variant="fill"` |
| `components/Modal.tsx` | 补 **焦点陷阱**：Tab / Shift+Tab 在弹层内循环，关闭后焦点归还触发元素 |

### 14.4 其它规范修正

- `TranscriberPage` 的 `window.confirm` → S2 `AlertDialog`（受控 `DialogTrigger`，只在模型未下载时才弹）。
- `TranscriptPane` 的时间轴行：`<div onClick>` → 原生 `<button>` + `focus-ring` + `aria-current`，键盘可达。
- `MarkdownArticle` 语言标签去掉 `text-transform: uppercase` + 宽字距（DESIGN-AUDIT §3 的规则此前漏网）。
- `ChatPane` 的 AI 回答去掉 `fontSize: 14` 覆盖，与笔记正文统一 16px / 1.7。
- `UsersPage` 表格单元格的 `lineHeight: '[18px]' / '[16px]'` 写死行高 → 保留（表格压行高的必要例外，已记入 §15）。
- `Page.tsx`：`Section` 的 `gap` 形参无人使用，改为固定 16；两处内联 `style={{}}` 改为 `style()` 宏。
- 全站清掉 **20 处内联 `style={{}}`** 与 **15 处在 JSX 里现调 `style()`**（改到模块顶层，避免每次渲染生成新类名）；
  剩下的 8 处是运行时动态值（面板宽度、logo 尺寸、markmap 高度）与 S2 图标的 `UNSAFE_style`，保留。
- `Page.tsx` 顶部注释里「分组标签用 Title S（14px bold）」是旧基线，与实现（`title` 16px/500）不符，已改正。

### 14.5 验证

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有的 react-refresh 警告）。
- `npm run build`：通过。
- 构建产物核对：新增的 `ListRow` / `Chip` / 焦点环样式均编译为 `light-dark()` 或 S2 语义 token。

### 14.6 顺带修掉的工程问题

- `.prettierrc` 里 `plugins: ["prettier-plugin-tailwindcss"]` 指向一个**并未安装**的包（Tailwind 早已移除），
  导致 `npx prettier` 直接报错退出。已删掉该行，prettier 现在可正常运行。
  被本次改动触及的 25 个文件已按项目 `.prettierrc` 重新格式化。
- `package.json` 里 `lucide-react`、`react-intersection-observer` 源码中零引用，已移除。
  顺带发现 `package-lock.json` 早于 `package.json`（缺 `qrcode` / `@types/qrcode`，`npm ci` 会失败），已重新生成。
  lockfile 里残留的 `lucide-react` 是 `@lobehub/*` 的传递依赖，属正常。

## 15. 遗留与待定

以下几项本轮**没有动**，需要确认后再改：

1. **`UsersPage.tsx` 表格单元格的 `lineHeight: '[18px]' / '[16px]'`**：
   与 §3「不覆盖行高」的规则冲突，但表格压行高是必要取舍，属于已批准的例外，未改。
2. **`AISettingsPage` 没有 `Section` 分组**：它是 Tabs 容器，内层 ModelPage / TranscriberPage 各自带 Section，无需再加。
3. **`NotFoundPage` / `LoginPage` / `OnboardingPage` 未走 `Page` 骨架**：
   三者都是脱离 app frame 的整屏页（登录、引导、404），用 `Page` 的 1200px 内容区反而不合适，保持现状。
4. **`Modal` 仍是自己实现的弹层**（S2 `DialogContainer` 在 React 19.2 下重复挂载）：
   本轮补了焦点陷阱与焦点归还，但焦点陷阱是手写的，不如 S2 `Dialog` 完整；
   若后续 S2 修复该问题，应换回官方组件。
5. **侧栏分组没有可见标题**：官方 S2 示例用 `SideNavHeader` 显示分组名（"Work"），
   S2 会在折叠时自动隐藏它。但本项目 §13 明确改成「无标题」以避免折叠时重排，
   两者只能二选一；当前维持「无标题」。

## 16. 2026-10-20 第四轮修正：侧栏几何与表格对齐官方规范

### 16.1 侧栏几何：查官方规范后改了什么

来源：
- [Spectrum · Browsing context: Side nav](https://spectrum.adobe.com/foundations/app-frame/side-navigation-browsing-context)
- [React Spectrum · SideNavPanel](https://react-spectrum.adobe.com/SideNavPanel)
- [Spectrum design data · side-navigation tokens](https://opensource.adobe.com/spectrum-design-data/tokens/side-navigation)

官方定义与本项目对照：

| 项 | 官方 | 修改前 | 修改后 |
| --- | --- | --- | --- |
| 展开宽度 | S2 示例 `width: 208` | 208 ✅ | 208 |
| 左右留白 | S2 示例写在面板自身 `marginX: 8` | 外层 wrapper `padding: 8`（**四边**） | 面板自身 `marginX: 8`，垂直不留白、面板通高 |
| 折叠宽度 | S2 内置 `--collapsedWidth: 42` | 42 ✅ | 42 |
| 分组间距 | token `side-navigation-item-to-header: 24px` | 8px（`!important` 覆盖） | 24px |

关键发现：**S2 `SideNavSection` 自带的 `marginTop: 24` 其实根本没生效** ——
它的条件写法是 `marginTop: {':not(:first-child)': 24}`，而 `style()` 宏只认
`default` / `isHovered` / `isPressed` 这类条件名，不认裸伪类选择器，构建产物里查不到 `margin-top: 24px`。
所以之前那 8px 完全是我们自己编的，且用 `!important` 压在一个并不存在的规则上面。
现在改成 24px（官方 token），并去掉 `!important`。

另外澄清一个规范误解：**app frame 规范里那条分隔线指的是侧栏底部「end section」与状态控制按钮之间的分隔**，
不是分组之间的分隔。我们没有 end section，所以分组之间不画线是对的。

### 16.2 表格：去掉自绘与双层边框

来源：[Spectrum · Table](https://spectrum.adobe.com/web/rsp/components/table#table)

S2 `TableView` **自带** 1px `gray-300` 边框 + 6px 圆角 + `gray-25` 底，
表头 `gray-75` 底 + 1px 下边框，单元格左右内边距 16px。
我们之前把 `TableView` 套在 `Panel variant="outline"`（1px `gray-200` + 10px 圆角）里 → **双层边框**。

改动：

| 位置 | 修改前 | 修改后 |
| --- | --- | --- |
| `UsersPage` 用户表 | `Panel outline` + `TableView` | 直接 `TableView`，`density="spacious"`（数据量少的设置表） |
| `KeysPage` 密钥表 | `Panel outline` + `TableView` | 直接 `TableView`，`density="compact"`（数据量大的管理表） |
| `TranscriberPage` 转写模型 | 自绘「面板 + 分隔行」列表 | 真正的 `TableView`（模型/状态/体积/操作） |
| `TranscriberPage` 自定义模型 | 自绘「面板 + 卡片行」列表 | 真正的 `TableView`（名称/仓库/状态/操作），`density="compact"` |
| `Panel` 组件 | 有 `fill` / `outline` 两个变体 | 删掉 `outline` 变体（表格不再需要，剩下没人用） |
| `MonitorPage` / `KeysPage` 小标签 | 自绘 tag / chip | S2 `Badge variant="gray" fillStyle="subtle"` |

`density` 按官方语义选：compact 32px 行（数据量大的管理面板）/ regular 40px（常规设置表）/ spacious 48px（数据量少的设置表）。

### 16.3 验证

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有的 react-refresh 警告）。
- `npm run build`：通过。

## 17. 2026-10-20 第五轮修正：表格 / 状态指示 / 角色标签

来源：
- [React Spectrum · TableView](https://react-spectrum.adobe.com/TableView.html)
- [React Spectrum · Badge](https://react-spectrum.adobe.com/Badge.html)

### 17.1 表格统一按官方 TableView 文档

官方文档给出的关键能力与本项目此前的偏差：

| 官方 | 本项目改前 | 改后 |
| --- | --- | --- |
| `styles={style({width: 'full'})}` | 没设宽度 | 全部加上 `tableStyles` |
| `overflowMode`（wrap/truncate） | 没显式指定 | 全部 `overflowMode="truncate"` |
| `density` | 各表不一（compact / regular / 默认） | 全部 `density="spacious"`（48px 行） |
| `loadingState="loading"` | 外层 `<Panel>` + `<LoadingState>` 另画一块 | 表格内显示进度圈 |
| `TableBody renderEmptyState` | 外层 `<Panel>` + `<EmptyState>` 另画一块 | 空态渲染在表格内部 |
| `selectionMode="none"` | 有 | 保留 |

改动后 4 张表（用户、API 密钥、转写模型、自定义模型）参数完全一致，
且不再外套 `Panel`，表格边框只有一层。

### 17.2 状态从圆点换成圆圈里的勾/叉/横线

S2 的 `StatusLight` 渲染的只是一个**实心圆点**（源码里 `light` 样式只有 `fill`），辨识度低。
改用 S2 自带的圆形状态图标 + `iconStyle` 宏：

```tsx
import {iconStyle} from '@react-spectrum/s2/style' with {type: 'macro'};
<CheckmarkCircleIcon styles={iconStyle({size: 'S', color: 'positive'})} />
```

`iconStyle` 内部把 `color` 写成 `--iconPrimary`（`fill` 类型），因此颜色仍然走语义 token，
明暗主题自动跟随，不需要自己写死色值。形状即语义：

| 形状 | 语义 | 用途 |
| --- | --- | --- |
| ✓ `CheckmarkCircle` | positive | 已下载 / 启用 / 正常 / 有效 / 成功 |
| ✗ `CloseCircle` | negative | 下载失败 / 不可达 / 已吊销 / 失败 |
| – `RemoveCircle` | notice / neutral | 下载中 / 未下载 / 停用 / 已过期 / 进行中 |

抽成共享件 `src/components/StatusIndicator.tsx`，替换了 6 处 `StatusLight`
（用户表、密钥表、转写模型、自定义模型、历史列表、部署监控）。

### 17.3 角色标签统一

改前：管理员是 `<Badge variant="neutral">`（有底色），普通用户是纯文本 —— 同一列两种呈现方式。
按 [Badge 官方规格](https://react-spectrum.adobe.com/Badge.html)：
Badge 是 "color-categorized metadata"，`variant` 决定底色语义、`fillStyle` 决定填充强度。
同一列的两种取值应该用**同一个组件 + 同一个 fillStyle**，只用 `variant` 区分强调程度：

```tsx
{user.is_superuser
  ? <Badge variant="accent" fillStyle="subtle">管理员</Badge>
  : <Badge variant="gray" fillStyle="subtle">普通用户</Badge>}
```

两者都有底色、同一形态、同一字重，只用颜色区分权限高低。
（`accent-subtle` = `light-dark(#e5f0fe,#0c2175)`、`gray-subtle` = `light-dark(#e9e9e9,#2c2c2c)`，明暗都自适应。）

### 17.4 验证

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有的 react-refresh 警告）。
- `npm run build`：通过。
- 构建产物核对：`--iconPrimary` 出现语义色
  `light-dark(rgb(7,147,85),…)`（positive）、`light-dark(rgb(240,56,35),…)`（negative）、
  `light-dark(rgb(212,91,0),…)`（notice）、`light-dark(rgb(143,143,143),…)`（neutral）；
  三个圆形图标 SVG 均已进包。

## 18. 2026-10-21 第六轮修正：角色下拉 / 删除用户 / 停用标签统一

来源：
- [React Spectrum · Picker](https://react-spectrum.adobe.com/Picker.html)
- [React Spectrum · Badge](https://react-spectrum.adobe.com/Badge.html)
- [Spectrum · Picker（isQuiet 的适用场景）](https://spectrum.adobe.com/web/rsp/components/picker)

### 18.1 角色列改成下拉框，去掉「设为管理员」按钮

改前：行尾有两个图标按钮（停用 / 设为管理员），改权限要先找到那个按钮再点确认弹窗，
角色列自己却是只读的标签 —— 同一件事分散在两个地方。

改后：角色列就是一个**安静型 Picker**（S2 的下拉框），点标签直接改权限：

```tsx
<Picker
  label={undefined}                       // 表格里不重复列头，语义交给 aria-label
  aria-label={`${name} 的角色`}
  isQuiet                                 // 表格/网格这类密集布局用 quiet 变体
  size="M"
  items={ROLE_OPTIONS}
  selectedKey={user.is_superuser ? 'admin' : 'user'}
  onSelectionChange={key => …}
  renderValue={items => <RoleBadge isSuperuser={items[0]?.id === 'admin'} />}>
  {item => <PickerItem id={item.id} textValue={item.label}>{item.label}</PickerItem>}
</Picker>
```

要点：
- **选值控件用 Picker，不给标签套按钮**。Spectrum 明确：quiet picker 适合
  「表格 / 网格」这类一行一个控件的密集布局（默认样式有底色，表格里会显得啰嗦）。
  之前用 `ActionButton` 包 `Badge` 的做法，控件的 grid 会把标签压到两行、文字溢出色块 —— 那是
  硬凑出来的交互，不是 Spectrum 的组件用法。
- **标签仍是 `Badge`**：用 Picker 官方的 `renderValue` 把选中值渲染成角色标签，
  保留 17.3 定的视觉语言 —— 两种角色共用同一组件 + 同一 `fillStyle`，只用 `variant` 区分。
  下拉列表里的选项保持纯文本（PickerItem 的常规用法；选项里塞 Badge 会把列表项挤成竖条）。
- 表格里不再重复一列「角色」文字标签，靠列头 + `aria-label` 说明这一格是什么；
  自己那一行整格禁用（不能降级自己），后端也补了同样的校验。
- 角色列宽 104 → 132：标签 + 控件内边距 + 单元格 32px 内边距，否则「普通用户」会被折行。

### 18.2 删除用户

行尾操作收敛为两个图标按钮：停用/启用、删除。删除走 S2 `AlertDialog`（`variant="destructive"`），
二次确认后才发 `DELETE /auth/admin/users/{id}`。

后端语义（`backend/app/routers/auth.py`）：
- 账号与他的 **API 密钥一并清除**（密钥等价于该用户的口令，留着等于留后门），
  用量明细跟着密钥走。`api_keys.user_id` 虽然声明了 `ondelete="CASCADE"`，
  但 SQLite 默认不执行外键约束，级联不会真的发生，所以显式删。
- **历史任务保留，只解除归属**（`video_tasks.user_id` 置空）—— 任务与产物是已经生成的笔记，
  不随账号一起消失。
- 不能删自己；执行者本身就是管理员，所以系统里始终至少留着一个管理员。

### 18.3 「已停用」统一为「停用」

状态名统一成两字：用户表 `启用 / 停用`，密钥表同一状态也改成 `停用`
（密钥表其余状态是 `有效 / 即将到期 / 已过期 / 已吊销`，按「状态名不带完成时」的规则对齐）。

### 18.4 验证

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有的 react-refresh 警告）。
- `npm run build`：通过。
- 1440 宽截图核对：角色列是 quiet Picker，标签单行不折行、右侧有下拉箭头、
  行尾两个图标按钮在列内、删除确认弹窗为 destructive 样式。
- 交互核对：点标签弹出下拉框（当前角色打勾）、选「普通用户」后角色更新并刷新、
  删除弹窗确认后该行从列表消失；明暗两套主题都看过。

> 附注：S2 的 `Picker` 在 dev 下会打 React Aria 的
> 「If you do not provide a visible label…」告警 —— S2 把 `label` 摘出来自己渲染
> `FieldLabel`，RAC 内部拿不到 label 就报警。密钥管理页等已有的 Picker 同样会打，
> 属于库自身的问题，不是本项目引入的。





## 19. 2026-10-21 第七轮修正：顶栏与内容区共用同一条右边线

来源：
- [Spectrum · App frame](https://spectrum.adobe.com/foundations/app-frame/overview)（含用户指定的 *App frame variants: Top App Bar (TAB)*）
- [Spectrum · Browsing context: Header](https://spectrum.adobe.com/foundations/app-frame/header-browsing-context)
- [Spectrum · Browsing context: Content area](https://spectrum.adobe.com/foundations/app-frame/content-area-browsing-context)
- [React Spectrum](https://react-spectrum.adobe.com/) 首页那个**真实渲染的 S2 app frame demo**（唯一可以直接量计算值的官方实现）

### 19.1 问题：顶栏操作比内容表面右缘更靠内

改前：`header.paddingEnd = 96`、`content.marginEnd = 80`，宽屏下最后一个全局操作
（账户按钮）右缘在 `1440 − 96 = 1344`，而内容表面右缘在 `1440 − 80 = 1360`。
两者差 16px，视觉上顶栏动作「吊」在卡片右缘以内，右侧收尾不对齐。
窄屏 `@media (max-width: 768px)` 又把两者分别改成 32 / 16，错位量同样是 16px。

根因是把规范里那张**示意插画**当成了像素标尺。那张图的右缘留白是带透视、
带投影的示意画法：我把原图放大到 2032×1200 逐像素量，卡片右缘到画框右缘
只有 **13px**（single-section 图），TAB 图里甚至只有 **1px** —— 图与图之间
自相矛盾，本身就不该被当成 80px 的依据。

### 19.2 依据：官方 demo 里三者完全重合

用 Playwright 量 react-spectrum.adobe.com 首页那个 S2 app frame demo：

| 元素 | 计算值 |
| --- | --- |
| frame 容器 | `left 208 / right 1232`（bg `rgb(248,248,248)`，radius 10） |
| header | `padding: 16px 16px 16px 20px`，高 64，通栏 208→1232 |
| 内容表面 | `left 272 / right 1216`，`margin: 0px 16px 0px 0px`，radius `16px 16px 0 0` |
| 末尾 quiet icon 按钮（Account） | `left 1184 / right 1216` |

结论：**frame 右缘 − 内容右缘 = 16px，且顶栏最后一个动作的右缘 == 内容表面右缘**。
header 的 `paddingEnd` 与 content 的 `marginEnd` 是同一个数（16），两者天然对齐。

### 19.3 改动

| 位置 | 修改前 | 修改后 |
| --- | --- | --- |
| `AppShell` `topbar.paddingEnd` | 96 | **16** |
| `AppShell` `content.marginEnd` | 80 | **16** |
| `global.css` `@media (max-width:768px)` | 顶栏 32 / 表面 16（`!important`） | **整块删除** |

删除断点覆盖的理由：16 / 16 这对值本身与视口宽度无关，宽窄屏都成立；
之前那个窄屏覆盖反而制造了同样的 16px 错位。真正需要按宽度变化的是侧栏折叠，
那部分由 `AppShell` 里的 JS 断点负责，与右边线无关。

### 19.4 验证

用 Playwright 在 1440 / 1280 / 1024 / 900 / 768 / 640 / 480 七档实测：

```
w=1440  contentRight=1424  lastActionRight=1424  aligned=true   padEnd=16px marginEnd=16px
w=1280  contentRight=1264  lastActionRight=1264  aligned=true   padEnd=16px marginEnd=16px
w=1024  contentRight=1008  lastActionRight=1008  aligned=true   padEnd=16px marginEnd=16px
w=900   contentRight=884   lastActionRight=884   aligned=true   padEnd=16px marginEnd=16px
w=768   contentRight=752   lastActionRight=752   aligned=true   padEnd=16px marginEnd=16px
w=640   contentRight=624   lastActionRight=624   aligned=true   padEnd=16px marginEnd=16px
w=480   contentRight=464   lastActionRight=464   aligned=true   padEnd=16px marginEnd=16px
```

七档全部 `aligned=true`，且 `scrollWidth == innerWidth`（无横向溢出）。
明暗两套主题都截图核对过，右上角收尾一致。

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有 react-refresh 警告）。
- `npx vite build`：通过。

> 说明：这里修的是 **app frame 层**的右边线（顶栏 vs 内容表面）。
> 内容区内部的 `NoteToolbar`（paddingX 24）与正文 `paper`（maxWidth 780 居中）
> 属于页面内部的两级布局，各自有自己的容器，不改动。


## 20. 2026-10-21 第八轮修正：启用/停用按钮并入状态列（行内开关）

来源：
- [Spectrum · App frame](https://spectrum.adobe.com/foundations/app-frame/overview)（用户指定的 *App frame variants: Top App Bar (TAB)*，含「Show/Hide 优先于 Expand/Collapse」「sentence case」等内容规范）
- [Spectrum · Switch](https://spectrum.adobe.com/web/rsp/components/switch)（anatomy / 何时用强调色 / 独立开关的适用条件 / content standards / 状态与键盘）
- [Spectrum · Table](https://spectrum.adobe.com/web/rsp/components/table)（density、列分隔线、对齐、行悬停）
- [Spectrum · Toast](https://spectrum.adobe.com/web/rsp/components/toast)（最多一个操作按钮、可操作 toast 不自动消失、不要同时弹多条）
- [Spectrum · Alert dialog](https://spectrum.adobe.com/web/rsp/components/alert-dialog)（使用要 sparingly、不要做过量确认）
- [React Spectrum · Switch](https://react-spectrum.adobe.com/Switch.html) / [ToggleButton](https://react-spectrum.adobe.com/ToggleButton.html)（组件 API 与语义分工）

### 20.1 问题：状态是只读的，开关藏在行尾

改前「状态」列是只读的 `StatusIndicator`（✓ 启用 / – 停用），真正能改状态的入口是行尾第 5 列的
锁形图标按钮（`Lock` / `LockOpen`，点开再走一个 `AlertDialog` 二次确认）：

| 问题 | 说明 |
| --- | --- |
| 同一件事两个位置 | 「现在是什么状态」和「怎么改状态」分在两列，读一行要在表格里横跨 200px 做对应 |
| 状态与控件语义割裂 | 只有一张标签在说「停用」，按钮却在另一列，且用锁图标（锁是权限语义，不是启用/停用） |
| 多一次无谓确认 | 停用是可逆操作（历史数据保留、随时可再启用），却要走一次 destructive 弹窗 |
| 列变宽 | 行尾 5 个交互位（角色 Picker + 2 个 28px 图标按钮）挤压名称列 |

### 20.2 依据：Spectrum 的组件语义分工

第一版用了 `Switch`，第二版改回 `Badge`。把两轮的核对过程都记下来，免得以后又绕回去。

**第一轮（Switch）**：按组件语义，每个用户确实是一个「可开关的设置」，符合
"Switches are best for communicating activation"。但 Switch 的 content standards 要求
标签描述**恒定的设置**（不能随状态变、不能写「开启/关闭」），于是标签只能写成
「允许登录」—— 用户反馈这四个字在中文里读起来像废话，信息密度低又不直观。
这是规范约束与中文语感冲突后的被迫产物，不是好的设计。

**第二轮（Badge）**：换回 Badge 后约束自然消失 —— Badge 表达的就是
**color-categorized metadata**（当前状态），内容写「启用 / 停用」才是它的正确用法。
同时：

| 维度 | Switch | Badge |
| --- | --- | --- |
| 占用 | 26×16 轨道 + 文字 ≈ 92px | 46×24 一枚标签 |
| 信息 | 需要读文字才知道是什么状态 | 颜色 + 两字，扫一眼就懂 |
| 中文语感 | 标签必须恒定，只能造「允许登录」 | 直接写当前状态，自然 |
| 语义 | 「这是一个设置」 | 「这是当前状态」（可点） |

最终采用 Badge + 原生 `<button>`：Badge 负责视觉与语义色，按钮负责可点性与键盘。

### 20.3 取消二次确认，改为「立即执行 + 可撤销」

Spectrum 对 AlertDialog 的要求是「use them sparingly … do not use for low-priority
notifications or excessive confirmations」。停用满足「可逆 + 数据保留 + 随时可撤回」，
因此**不该**用确认框，改用 Toast 的单一操作按钮（撤销）：

- 规范：每个 toast 最多一个按钮，且可操作的 toast 不会自动消失（WCAG 2.2.1）；
  因此 `toast.successAction` 不传 `timeout`，由 `shouldCloseOnAction` 在点击后关闭。
- 撤销后**不再**叠一个「撤销」toast（否则可无限来回刷屏，规范也要求避免同时弹多条），
  只给一句普通确认；真正让用户确认撤销生效的是 Badge 自己翻回原状。
- 删除用户仍保留 `AlertDialog`（destructive，不可撤销）。

### 20.4 实现要点

| 项 | 做法 |
| --- | --- |
| 组件 | S2 `Badge`（`variant='positive'` 绿 / `'neutral'` 灰，`fillStyle="subtle"`）+ 外层原生 `<button>` |
| 语义色 | 用 `positive` 而不是色阶 `green`：`positive` 是语义 token（成功/可用），暗色下自动取 `positive-subtle` 深绿；`green` 只是色板档位，不表达「可用」 |
| 标签 | 可见文字就是当前状态「启用 / 停用」；`aria-label` = `${用户名}：${状态}`，配 `aria-pressed` |
| 列宽 | 状态列 132 → **96**（Badge ≈ 46px + 32px 单元格内边距） |
| 角色列 | 去图标、去箭头、标签等宽 84px 后 132 → **116** |
| 角色标签 | 「管理员」3 字 / 「普通用户」4 字，内容宽度不同会参差：`styles={width: 84}` 定宽 + `UNSAFE_style={{justifyContent:'center'}}` 居中（`justifyContent` 不在 Badge 的允许覆盖项里，只能走 inline） |
| 去下拉箭头 | S2 Picker 没有关闭 chevron 的开关（Chevron 是按钮里 10×10 的固定结构）。用 `UNSAFE_className="role-picker"` + `global.css` 里 `.role-picker button > svg { display: none }`，同时把按钮 `padding-inline-end` 归零，否则右侧会空一截。注意 `UNSAFE_className` 落在 Picker 的**外层 div**，按钮是子元素，选择器要多一层 |
| 删除按钮 | `ActionButton` 没有 danger/negative 变体（只有 `Button` 有 `negative`），但图标吃 `currentColor`：`.delete-action` 直接把颜色设成 S2 的 negative 语义值（light `#b72818` / dark `#ff6756`，取自构建产物，与 StatusIndicator、AlertDialog destructive 同源），hover 用深一档 `#d73220` / `#fc432e`，禁用态回落 `#cacaca` / `#4a4a4a` |
| 行尾 | 只剩删除一个 24px 图标按钮，列宽 96 → **64** |
| 自己那一行 | `<button disabled>`（后端也拒绝停用自己），`.status-chip:disabled` 降到 50% 不透明度 + 默认光标 |
| hover | `.status-chip:hover` 降到 72% 不透明度；**不铺灰底**（表格行已有 hover 高亮，叠起来显脏） |
| Badge 宽度 | Badge 是 `display:flex`，直接放进单元格会被 `width: full` 拉满整格，外层必须包 `width: fit` |
| 乐观更新 | 点下立即变色，不等服务端；失败回滚到服务端已知值 |
| 乱序保护 | 每行一个状态切换队列：同一时刻只飞一个 `PATCH`，连点时只记「最后要变成什么」，队列跑完再提示。避免两个相反请求到达顺序不确定导致「标签显示 A、库里是 B」 |
| 撤销 | `toast.successAction('已停用 X', '撤销', …)` 反向再调一次同一个函数 |

**踩到的两个宏坑**（都只在运行时/构建时才暴露）：

1. `style()` 宏一旦带条件值（`isHovered` / `disabled` 之类）就返回**函数**，只能交给组件的
   render prop。直接当 `className` 用会被字符串化成函数源码，样式全部失效 ——
   表现是 hover 时露出浏览器原生按钮的灰框。所以 `.status-chip` 的 hover / disabled
   写进 `global.css`，宏里只留无条件值。
2. `filter: brightness()` 不在 S2 的允许值白名单里（`filter` 只接受几个预设 `drop-shadow`），
   构建期就会报 `Invalid style value`。换用 `opacity` 表达 hover 反馈。

### 20.5 顺带修掉：空表看不见空态

`UsersPage` 之前漏了全站规则 11 的 `.table-empty` 包裹 —— S2 虚拟化表格在没有任何数据时把表体
压成 0 高度，`renderEmptyState` 的内容被定位到可视区之外，页面上只剩表头。已补：

```tsx
<div className={rows.length === 0 && !isLoading ? 'table-empty' : undefined}>
  <TableView …>
```

### 20.6 验证

Playwright（1440 / 1280 × 明暗两套主题，mock `/api/auth/admin/users`）：

| 检查 | 结果 |
| --- | --- |
| 状态列是一枚 Badge，行尾只剩删除按钮 | ✅ 列宽 96 / 64，表头对齐，无横向溢出（`scrollWidth == innerWidth`） |
| 点 Badge → `PATCH {is_active}` | ✅ 只发一次，body 正确，`aria-pressed` 同步翻转 |
| 亮/暗两套配色 | ✅ `positive-subtle` 浅绿 `rgb(215,247,225)` / 暗色深绿；停用为 `gray-subtle` |
| 自己那一行 | ✅ `disabled=true`、`opacity .5`、`cursor: default`，点击与 `Enter` 都不发请求 |
| 键盘 | ✅ `Tab` / `Enter` 可切换；`.focus-ring` 可见焦点环 |
| 鼠标 | ✅ `cursor: pointer`（行内 pointer 被宏类覆盖过，已改为 CSS 类） |
| 撤销 | ✅ 撤销按钮只出现一次；点后反向 PATCH 发出，Badge 翻回，toast 变普通确认 |
| 请求失败 | ✅ 拦截器提示错误，Badge 回滚到真实值 |
| 连点乱序 | ✅ 两个 PATCH 串行，最终视觉与最后一次意图一致 |
| 空表 | ✅ 表头 + 插画空态可见 |
| 角色列 | ✅ 去图标、去下拉箭头，两个标签等宽 84px 且文字居中（3 字与 4 字左右缘对齐） |
| 角色下拉仍可用 | ✅ 点 Badge 弹出管理员 / 普通用户，选中后 `PATCH {is_superuser}` 正常 |
| 删除按钮 | ✅ 常态 `rgb(183,40,24)`（S2 negative），hover 加深；自己那一行仍是禁用灰 `rgb(202,202,202)` |
| 删除确认 | ✅ 点开仍是 destructive AlertDialog（「删除用户 X？」+ 不可撤销说明） |
| 无控制台报错 | ✅ 点击、撤销、键盘操作全程 0 error |

> 说明：用户要求里的链接指向 *App frame variants: Top App Bar (TAB)* 一节。该节讲的是
> 9 宫格产品切换器与侧栏状态控制按钮的位置，与「表格里合并启用/停用」没有直接关系；
> 本次真正约束实现的是同一份 app frame 文档里的内容规范（Show/Hide 用词、sentence case、
> 术语不描述控件外观），以及上面列的 Badge / Table / Toast / Alert dialog 组件规范。

## 20. 2026-10-21 第八轮修正：侧栏与内容区之间不留空隙

来源：
- [Spectrum · App frame](https://spectrum.adobe.com/foundations/app-frame/overview)
- [Spectrum · Browsing context: Side nav](https://spectrum.adobe.com/foundations/app-frame/side-navigation-browsing-context)
- [React Spectrum](https://react-spectrum.adobe.com/) 首页真实 app frame demo（点开侧栏展开态实测）
- `@react-spectrum/s2` 包内 `src/SideNav.tsx` / `src/SideNavPanel.tsx` 源码

### 20.1 问题：侧栏与内容表面之间空了一大块

改前实测（1440 宽）：

| 量 | 值 |
| --- | --- |
| 侧栏面板 | 8 → 216（`width: 208` + `marginX: 8`） |
| 导航项点击区 | 8 → **188.3**（只占 180px） |
| 内容表面左缘 | **224** |
| 面板右缘 → 内容左缘 | **8px** |
| 导航项右缘 → 内容左缘 | **35.7px**（视觉上真正看得到的空档） |

也就是说：表面边距 8px + 面板内部 27.7px 的死区，叠出来约 36px 的空当。
用户说的「空隙太大」指的是后面这个更大的数。

### 20.2 两个根因

**⑴ `marginX: 8` 抄错了地方。**
它来自官方文档里 **SideNavPanel 独立组件示例**的
`styles={style({gridArea: 'sidebar', width: 208, marginX: 8})}` —— 那是「单个面板
怎么摆」的示范，不是 app frame 的组装配方。实测 Adobe 首页那个真 app frame：
侧栏面板左缘直接贴住 frame 左缘（外侧无 margin），**面板右缘 === 内容表面左缘（gutter 0）**。
展开态实测：panel `208 → 340`，content `340 → 1216`，`gutter = 0`。

**⑵ S2 隐藏 chevron 仍占宽度。**
`SideNavItem` 会为「可展开子级」预留 32px 的 chevron 槽位。本项目一级导航没有子级，
S2 于是用 `visibility: hidden` 隐藏它 —— **看不见，但布局宽度照占**。
源码 `SideNav.tsx` 的 `expandButton` 样式里写的是
`visibility: {isHidden: 'hidden'}`，而不是 `display: none`。
实测该槽位拿走 27.7px，导航项因此只撑到面板内容宽的 87%：
`180.3 / 208`。

### 20.3 改动

| 位置 | 修改前 | 修改后 |
| --- | --- | --- |
| `AppShell` `navPanel` | `marginX: 8` | **`marginStart: 8` + `marginEnd: 0`** |
| `global.css` | — | 新增 **`.app-nav [slot='chevron'] { display: none }`** |

- 左边保留 8px：侧栏不贴死窗口边缘，与顶栏品牌 16px 内边距形成同一族的左侧节奏。
- 右边归零：内容表面直接接管面板右缘，app frame 的两栏从此是「贴合」而非「留缝」。
- chevron 槽位改 `display: none`：不是我们自造样式，而是把 S2 因为「没有子级」
  本来就隐藏的东西从**布局**里也去掉。折叠态 S2 原生就是 `display: none`，
  这个覆盖不改变折叠表现。

### 20.4 验证

实测（Playwright，六档宽度）：

```
w=1440  panel=8..216   contentL=216  gutter=0  rowToContent=0  rightAligned=true  overflowX=false
w=1280  panel=8..216   contentL=216  gutter=0  rowToContent=0  rightAligned=true  overflowX=false
w=1024  panel=8..216   contentL=216  gutter=0  rowToContent=0  rightAligned=true  overflowX=false
w=900   panel=8..216   contentL=216  gutter=0  rowToContent=0  rightAligned=true  overflowX=false
w=768   panel=8..50    contentL=50   gutter=0  rowToContent=0  rightAligned=true  overflowX=false
w=640   panel=8..50    contentL=50   gutter=0  rowToContent=0  rightAligned=true  overflowX=false
```

- 导航项宽度：`180.3 → 192`（撑满面板内容宽，`208 − 16` 的图标缩进）
- 面板右缘 → 内容左缘：`8 → 0`
- 导航项右缘 → 内容左缘：`35.7 → 0`
- 上一轮修好的顶栏/内容右缘对齐仍然成立（`rightAligned=true` 六档全过）
- 折叠态（≤768）面板 42px、内容紧贴，行为正常
- 明暗两套主题、七个页面逐页截图核对

- `npx tsc -b`：通过。
- `npx eslint src`：通过（仅剩 `Page.tsx` 两条既有 react-refresh 警告）。
- `npx vite build`：通过。

> 附注：导航图标 x=20，顶栏 logo x=24，差 4px。这是 S2 条目自身的 12px 缩进
> 决定的，不是这次要修的对齐问题。
>
> 关于左侧那 8px 边距：Adobe 首页 hero 里侧栏是**贴住 frame 左缘**（无 margin），
> 靠面板内部的 16px padding 提供呼吸感。但我们的面板没有内部 padding（S2
> SideNavPanel 自身是零 padding，padding 由调用方给），如果直接贴边，选中态的
> 2px 指示条会贴在窗口最左侧（实测 x=12，紧贴边缘），视觉上不好看；
> 而如果照搬 Adobe 的 `padding-inline: 16px`，行宽会超出面板（实测 224 > 208），
> 把内容表面压到 row 右缘之外。所以这里保留左边 8px / 右边 0 的不对称做法：
> 它同时满足「内容表面紧贴侧栏」和「左侧不贴死窗口」两个目标。
> 右侧才是用户所说的「空隙」，已归零。
