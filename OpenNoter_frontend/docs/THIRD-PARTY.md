# 图标、字体与第三方资源

## UI / 操作图标

- 来源：`@react-spectrum/s2/icons`（Adobe React Spectrum 2）
- 许可：Apache License 2.0
- 版本：`@react-spectrum/s2@1.8.2`
- 使用方式：表单、表格、工具栏、导航等全部直接使用 S2 图标组件；侧栏也使用同一套 `@react-spectrum/s2/icons` 原生组件。
- 归属：图标路径数据版权归 Adobe，按 Apache-2.0 使用；原始许可见 `node_modules/@react-spectrum/s2/LICENSE`。
- 未使用：Font Awesome、Material Icons、Lucide、Ant Design Icons 等第三方 UI 图标集。`package.json` 中仍保留的 `lucide-react` 已无源码引用。

## 平台 / 供应商品牌标

- B 站、YouTube、抖音、快手、本地视频：`src/components/Icons/platform.tsx` 内的 SVG，属于对应平台的品牌标识；仅用于识别平台，版权归各平台所有。
- AI 供应商：`@lobehub/icons`（MIT）的 Mono 变体，品牌标识版权归各厂商所有；未知供应商回退到项目自带的 `src/assets/customAI.png`。
- OpenNoter 自身 logo：`src/assets/icon.svg`，项目自有资源。

## 字体

- 思源黑体（Source Han Sans / Noto Sans CJK SC）：Regular / Medium / Bold
- 思源宋体（Source Han Serif / Noto Serif CJK SC）：Regular
- 思源等宽（Source Han Mono / Noto Sans Mono CJK SC）：Regular
- 许可：SIL Open Font License 1.1，见 `public/fonts/LICENSE-NotoCJK.txt`
- 字体已做常用字子集（约 7916 字），仅用于本项目 UI；未加载 Adobe Clean、PingFang、SimSun、Fira Code、KaTeX 等其他字体。

## 其他

- Markdown 渲染：`react-markdown`、`remark-gfm`、`remark-math`、`rehype-katex`、`rehype-slug`（MIT）
- 代码高亮：`react-syntax-highlighter`（MIT），使用 oneLight 主题
- 思维导图：`markmap-lib` / `markmap-view`（MIT）；`markmap-toolbar` 已不再使用
- 图片缩放：`react-medium-image-zoom`（BSD-3-Clause）
