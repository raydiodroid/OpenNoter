import { useState } from 'react'
import { Link, LinkButton, Tab, TabList, TabPanel, Tabs, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CodeIcon from '@react-spectrum/s2/icons/Code'
import DownloadIcon from '@react-spectrum/s2/icons/Download'
import LinkIcon from '@react-spectrum/s2/icons/Link'

import { Page, PageHeader, Panel, Section } from '@/components/Page'
import { Notice } from '@/components/Notice'
import logo from '@/assets/icon.svg'

/**
 * 关于页：产品说明 + 快捷入口。
 *
 * 内容约定：
 * - 版本号只从 `__APP_VERSION__` 取（构建期由 vite.config.ts 注入，回退到
 *   src-tauri/tauri.conf.json 的 version），页内不写死任何版本字符串。
 * - 功能描述必须与后端/前端真实实现一致；导出能力只写 Markdown，
 *   PDF / Word 尚未实现，不要写成已完成。
 */

const REPO_URL = 'https://github.com/raydiodroid/OpenNoter'
const RELEASES_URL = `${REPO_URL}/releases`
const ISSUES_URL = `${REPO_URL}/issues`
const CHANGELOG_URL = `${REPO_URL}/blob/master/CHANGELOG.md`

/** 视频平台：与 backend/app/services/constant.py 的 SUPPORT_PLATFORM_MAP 一致 */
const PLATFORMS = [
  { name: '哔哩哔哩', desc: '字幕优先，支持 av / BV、分 P 与短链' },
  { name: 'YouTube', desc: '字幕优先，403 时自动切换播放器客户端重试' },
  { name: '抖音', desc: '无需登录，取无水印视频流后抽音轨' },
  { name: '快手', desc: '直连 CDN，时长单位自动换算' },
  { name: '本地视频', desc: '上传文件后抽取音轨与封面' },
]

/** 转写引擎：与 backend/app/transcriber/transcriber_provider.py 的 TranscriberType 一致 */
const ENGINES = [
  { name: 'Faster Whisper', kind: '本地', desc: 'CUDA 可用时自动走 GPU，否则 CPU int8' },
  { name: 'MLX Whisper', kind: '本地', desc: '仅 Apple Silicon macOS 可用' },
  { name: 'Groq 兼容端点', kind: '在线', desc: '任意 OpenAI 兼容的 /audio/transcriptions 服务' },
  { name: '必剪', kind: '在线', desc: 'B 站必剪字幕接口' },
  { name: '快手', kind: '在线', desc: '快手字幕接口' },
]

const FEATURES = [
  { title: '多平台解析', desc: '哔哩哔哩、YouTube、抖音、快手与本地视频共五类来源。' },
  { title: '字幕优先', desc: '站点有字幕时直接取字幕，跳过音频下载与转写整段流程。' },
  { title: '五种转写引擎', desc: '本地 Whisper 系列与在线端点可随时切换，模型可自定义。' },
  { title: '任意模型', desc: '所有 OpenAI 兼容接口都能接入，本地 Ollama 同样可用。' },
  { title: '笔记风格', desc: '精简、详细、教程、学术、小红书等 9 种写作风格。' },
  { title: '笔记格式', desc: '目录、原片跳转、原片截图、AI 总结，提交前勾选。' },
  { title: '多模态理解', desc: '按间隔采样关键帧交给多模态模型，画面也参与总结。' },
  { title: '结构化产出', desc: '生成带层级与锚点的 Markdown，可一键复制或导出 .md。' },
  { title: '原文参照', desc: '转写按时间轴分段，与笔记并排对照阅读。' },
  { title: 'AI 问答', desc: '向量检索加函数调用，回答附原文出处与时间点。' },
  { title: '思维导图', desc: '由笔记自动生成脑图，可导出 XMind / SVG / PNG。' },
  { title: '多用户与密钥', desc: '账号登录、OIDC 单点登录、API Key、配额与部署监控。' },
]

const STACK = [
  { term: '后端', desc: 'FastAPI + SQLite（WAL）+ ChromaDB，任务跑在进程内线程池' },
  { term: '前端', desc: 'React 19 + Vite + Adobe Spectrum 2' },
  { term: '桌面端', desc: 'Tauri 2，后端以 sidecar 进程随应用启动' },
  { term: '浏览器插件', desc: 'Vue 3 + Manifest V3（Chrome / Edge / Firefox）' },
]

const hero = style({
  display: 'flex',
  alignItems: 'center',
  gap: 16,
  flexWrap: 'wrap',
})
const heroText = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  minWidth: 0,
  flexGrow: 1,
})
const heroTitle = style({ font: 'heading-sm', color: 'neutral', margin: 0 })
const heroSub = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const actions = style({ display: 'flex', gap: 8, flexWrap: 'wrap' })
const paragraph = style({ font: 'body', color: 'neutral', margin: 0, maxWidth: 840 })
const grid = style({
  display: 'grid',
  gridTemplateColumns: '[repeat(auto-fit,minmax(280px,1fr))]',
  gap: 12,
})
const cardTitle = style({ font: 'title', color: 'neutral', margin: 0, marginBottom: 4 })
const cardText = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const code = style({
  font: 'code-sm',
  color: 'neutral',
  backgroundColor: 'base',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderRadius: 'default',
  padding: 16,
  margin: 0,
  overflowX: 'auto',
  whiteSpace: 'pre',
})
const logoMark = style({ borderRadius: 'lg' })

/** 行内代码：等宽字体 + 浅底，跟随 S2 的 code-sm token */
const inlineCode = style({
  font: 'code-sm',
  color: 'neutral',
  backgroundColor: 'layer-2',
  borderRadius: 'sm',
  paddingX: 4,
  paddingY: 2,
})

/** 明细行：左侧固定宽度的名词，右侧说明自动换行 */
const rowList = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const row = style({ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 8 })
const rowTerm = style({ font: 'body', color: 'neutral', margin: 0, minWidth: 132 })
const rowDesc = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
  flexGrow: 1,
  flexBasis: 220,
})

/** 名词 + 类型徽标（本地 / 在线） */
const termGroup = style({
  display: 'flex',
  alignItems: 'baseline',
  gap: 8,
  minWidth: 132,
})
const kindTag = style({
  font: 'ui-sm',
  color: 'neutral-subdued',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderRadius: 'full',
  paddingX: 8,
  paddingY: 2,
})

const tabContent = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  paddingTop: 16,
})
const linkList = style({ display: 'flex', flexDirection: 'column', gap: 12 })
const linkRow = style({ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 8 })
const linkTerm = style({ font: 'body', color: 'neutral', margin: 0, minWidth: 132 })
const footer = style({
  paddingTop: 24,
  textAlign: 'center',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 1,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

export default function AboutPage() {
  const version = __APP_VERSION__
  const [tab, setTab] = useState('source')

  return (
    <Page>
      <PageHeader
        title="关于 OpenNoter"
        description="把视频变成结构化笔记的开源工具。可自部署、可桌面端离线运行，也提供 HTTP API 供脚本与 agent 调用。"
      />

      <Panel>
        <div className={hero}>
          <img src={logo} alt="OpenNoter" width={48} height={48} className={logoMark} />
          <div className={heroText}>
            <h2 className={heroTitle}>OpenNoter</h2>
            <p className={heroSub}>v{version} · MIT License</p>
          </div>
          <div className={actions}>
            <LinkButton href={REPO_URL} target="_blank" variant="accent">
              <CodeIcon />
              <Text>GitHub 仓库</Text>
            </LinkButton>
            <LinkButton href={RELEASES_URL} target="_blank" variant="secondary">
              <DownloadIcon />
              <Text>下载桌面版</Text>
            </LinkButton>
            <LinkButton href={ISSUES_URL} target="_blank" variant="secondary">
              <LinkIcon />
              <Text>问题反馈</Text>
            </LinkButton>
          </div>
        </div>
      </Panel>

      <Section title="项目简介">
        <p className={paragraph}>
          OpenNoter 是一个开源的 AI
          视频笔记助手。粘贴视频链接后，它会按需取字幕或转写音频、调用大模型总结， 产出结构清晰的
          Markdown 笔记，并支持插入关键帧截图与原片时间跳转。生成的笔记会建立向量索引，
          可以直接对内容提问，回答带原文出处。
        </p>
        <p className={paragraph}>
          模型与转写引擎都可以在设置里自由切换：既能接云端服务，也能完全本地运行——数据不出机器。
        </p>
      </Section>

      <Section title="功能特性">
        <div className={grid}>
          {FEATURES.map(feature => (
            <Panel key={feature.title}>
              <h3 className={cardTitle}>{feature.title}</h3>
              <p className={cardText}>{feature.desc}</p>
            </Panel>
          ))}
        </div>
      </Section>

      <Section title="支持的来源">
        <div className={grid}>
          <Panel>
            <h3 className={cardTitle}>视频平台</h3>
            <div className={rowList}>
              {PLATFORMS.map(item => (
                <div key={item.name} className={row}>
                  <p className={rowTerm}>{item.name}</p>
                  <p className={rowDesc}>{item.desc}</p>
                </div>
              ))}
            </div>
          </Panel>
          <Panel>
            <h3 className={cardTitle}>音频转写引擎</h3>
            <div className={rowList}>
              {ENGINES.map(item => (
                <div key={item.name} className={row}>
                  <div className={termGroup}>
                    <p className={rowTerm}>{item.name}</p>
                    <span className={kindTag}>{item.kind}</span>
                  </div>
                  <p className={rowDesc}>{item.desc}</p>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </Section>

      <Section title="快速开始">
        <Panel>
          <Tabs
            aria-label="运行方式"
            selectedKey={tab}
            onSelectionChange={key => setTab(String(key))}
          >
            <TabList aria-label="运行方式">
              <Tab id="source">源码运行</Tab>
              <Tab id="docker">Docker 部署</Tab>
              <Tab id="desktop">桌面版</Tab>
            </TabList>

            <TabPanel id="source">
              <div className={tabContent}>
                <pre className={code}>{`git clone https://github.com/raydiodroid/OpenNoter.git
cd OpenNoter
cp .env.example .env`}</pre>
                <pre className={code}>{`cd backend
pip install -r requirements.txt
python main.py                # 监听 0.0.0.0:8483`}</pre>
                <pre className={code}>{`cd OpenNoter_frontend
npm ci
npm run dev                   # http://localhost:3015`}</pre>
                <p className={paragraph}>
                  前端仓库只跟踪 package-lock.json，用 npm 安装依赖；开发端口 3015，会把 /api 与
                  /static 代理到后端 8483。
                </p>
              </div>
            </TabPanel>

            <TabPanel id="docker">
              <div className={tabContent}>
                <pre className={code}>{`git clone https://github.com/raydiodroid/OpenNoter.git
cd OpenNoter
cp .env.example .env          # 必填：compose 的端口变量没有默认值
docker compose up --build -d`}</pre>
                <p className={paragraph}>
                  默认通过 <code className={inlineCode}>APP_PORT</code>（3015）对外访问，即{' '}
                  <code className={inlineCode}>http://localhost:3015</code>
                  。数据库、配置、截图与模型都挂载到宿主机 子目录，重建容器不会丢。
                </p>
                <p className={paragraph}>
                  GPU 转写用 {''}
                  <code className={inlineCode}>
                    docker compose -f docker-compose.gpu.yml up --build -d
                  </code>
                  ，需要 NVIDIA 显卡与 Container Toolkit。
                </p>
              </div>
            </TabPanel>

            <TabPanel id="desktop">
              <div className={tabContent}>
                <p className={paragraph}>
                  桌面客户端支持 macOS 与 Windows，后端以 sidecar 进程随应用启动，无需另开服务。
                  安装包在 <Link href={RELEASES_URL}>Releases</Link> 页面获取。
                </p>
                <p className={paragraph}>
                  Windows 上请把程序装在「不含中文与空格、且可写」的路径，否则后端可能无法启动。
                </p>
              </div>
            </TabPanel>
          </Tabs>
        </Panel>
      </Section>

      <Section title="首次使用前">
        <Notice variant="notice">
          <span>
            认证默认开启，且没有注册入口。全新部署必须先创建一个账号，否则无法登录——详见下方命令。
          </span>
        </Notice>
        <Panel>
          <div className={tabContent}>
            <p className={paragraph}>
              在 <code className={inlineCode}>.env</code>{' '}
              里至少设置一个签名密钥，否则登录与凭据加解密都会报错：
            </p>
            <pre className={code}>{`OPENNOTER_JWT_SECRET=<长随机串>
# 生成方式（任选其一）
python -c "import secrets;print(secrets.token_urlsafe(48))"`}</pre>
            <p className={paragraph}>
              然后创建首个管理员（compose 部署的容器名是 opennoter-backend）：
            </p>
            <pre className={code}>{`docker exec -it opennoter-backend \\
  python manage.py create-user you@example.com --admin`}</pre>
            <p className={paragraph}>
              单机内网自托管时，也可以设{' '}
              <code className={inlineCode}>OPENNOTER_AUTH_ENFORCE=0</code> 关闭认证；
              公网部署不建议关闭。网页端「用户管理」页可以调整角色、启停与删除账号，新建账号仍需走上面的命令行。
            </p>
          </div>
        </Panel>
      </Section>

      <Section title="技术栈">
        <Panel>
          <div className={rowList}>
            {STACK.map(item => (
              <div key={item.term} className={row}>
                <p className={rowTerm}>{item.term}</p>
                <p className={rowDesc}>{item.desc}</p>
              </div>
            ))}
          </div>
        </Panel>
      </Section>

      <Section title="相关链接">
        <Panel>
          <div className={linkList}>
            <div className={linkRow}>
              <p className={linkTerm}>源代码</p>
              <Link href={REPO_URL}>github.com/raydiodroid/OpenNoter</Link>
            </div>
            <div className={linkRow}>
              <p className={linkTerm}>问题反馈</p>
              <Link href={ISSUES_URL}>GitHub Issues</Link>
            </div>
            <div className={linkRow}>
              <p className={linkTerm}>版本发布</p>
              <Link href={RELEASES_URL}>Releases（桌面端安装包）</Link>
            </div>
            <div className={linkRow}>
              <p className={linkTerm}>更新日志</p>
              <Link href={CHANGELOG_URL}>CHANGELOG.md</Link>
            </div>
            <div className={linkRow}>
              <p className={linkTerm}>接口文档</p>
              <Text styles={cardText}>
                服务地址后加 <code className={inlineCode}>/api/</code> 即为免鉴权的自描述指引（返回
                JSON；带 <code className={inlineCode}>Accept: text/markdown</code> 返回
                Markdown），含完整端点清单
              </Text>
            </div>
            <div className={linkRow}>
              <p className={linkTerm}>许可证</p>
              <Text styles={cardText}>
                MIT License · Copyright (c) 2024 Jeffery Huang · Copyright (c) 2026 raydiodroid
              </Text>
            </div>
          </div>
        </Panel>
      </Section>

      <footer className={footer}>
        <Text styles={paragraph}>
          欢迎 <Link href={ISSUES_URL}>提 issue</Link>、<Link href={REPO_URL}>Star</Link> 或 PR。
        </Text>
      </footer>
    </Page>
  )
}
