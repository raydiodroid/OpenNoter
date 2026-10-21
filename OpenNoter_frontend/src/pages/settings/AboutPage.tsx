import { useState } from 'react'
import { Link, LinkButton, Tab, TabList, TabPanel, Tabs, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CodeIcon from '@react-spectrum/s2/icons/Code'
import DownloadIcon from '@react-spectrum/s2/icons/Download'
import OpenInIcon from '@react-spectrum/s2/icons/OpenIn'

import { Page, PageHeader, Panel, Section } from '@/components/Page'
import logo from '@/assets/icon.svg'

const FEATURES = [
  { title: '多平台支持', desc: 'B 站、YouTube、抖音、快手与本地视频。' },
  { title: '笔记格式', desc: '目录、原片跳转、关键帧截图、AI 总结自由组合。' },
  { title: '笔记风格', desc: '精简、详细、教程、学术、小红书等 9 种风格。' },
  { title: '多模态理解', desc: '按间隔采样关键帧交给多模态模型，理解更准确。' },
  { title: '自有模型', desc: '任意 OpenAI 兼容供应商，本地或云端。' },
  { title: '本地转写', desc: 'Faster Whisper / MLX Whisper，也支持在线端点。' },
  { title: '结构化笔记', desc: '自动生成带层级、带锚点的 Markdown。' },
  { title: '原文参照', desc: '逐段时间轴，和笔记对照阅读。' },
  { title: 'AI 问答', desc: '基于笔记与转写内容做检索问答。' },
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
/** 社区卡片：单卡片，限宽避免二维码区域被拉宽 */
const community = style({
  display: 'grid',
  gridTemplateColumns: '[minmax(0,320px)]',
  gap: 16,
})
/** 二维码卡片内容：垂直居中 */
const qrContent = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 8,
  textAlign: 'center',
})
/** 二维码占位：真实二维码图就位后替换此区域 */
const qrPlaceholder = style({
  width: 180,
  height: 180,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: 'default',
  borderWidth: 1,
  borderStyle: 'dashed',
  borderColor: 'gray-400',
  backgroundColor: 'base',
})
const qrPlaceholderText = style({ font: 'detail', color: 'neutral-subdued' })

const logoMark = style({ borderRadius: 'lg' })

/** Tab 面板内容 */
const tabContent = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  paddingTop: 16,
})
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
  const [tab, setTab] = useState('manual')

  return (
    <Page>
      <PageHeader
        title="关于 OpenNoter"
        description="把视频变成结构化笔记的开源工具。可自部署，也可用桌面版离线运行。"
      />

      <Panel>
        <div className={hero}>
          <img src={logo} alt="OpenNoter" width={48} height={48} className={logoMark} />
          <div className={heroText}>
            <h2 className={heroTitle}>OpenNoter</h2>
            <p className={heroSub}>v{version} · MIT License</p>
          </div>
          <div className={actions}>
            <LinkButton href="https://www.opennoter.app" target="_blank" variant="accent">
              <OpenInIcon />
              <Text>在线体验</Text>
            </LinkButton>
            <LinkButton
              href="https://github.com/JefferyHcool/BiliNote"
              target="_blank"
              variant="secondary"
            >
              <CodeIcon />
              <Text>GitHub 仓库</Text>
            </LinkButton>
            <LinkButton
              href="https://github.com/JefferyHcool/BiliNote/releases"
              target="_blank"
              variant="secondary"
            >
              <DownloadIcon />
              <Text>下载桌面版</Text>
            </LinkButton>
          </div>
        </div>
      </Panel>

      <Section title="项目简介">
        <p className={paragraph}>
          OpenNoter 是一个开源的 AI
          视频笔记助手。粘贴视频链接后，它会自动下载音频、转写文字、调用大模型总结， 产出结构清晰的
          Markdown 笔记，并支持插入关键帧截图与原片时间跳转。
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

      <Section title="快速开始">
        <Panel>
          <Tabs
            aria-label="部署方式"
            selectedKey={tab}
            onSelectionChange={key => setTab(String(key))}
          >
            <TabList aria-label="部署方式">
              <Tab id="manual">手动安装</Tab>
              <Tab id="docker">Docker 部署</Tab>
            </TabList>
            <TabPanel id="manual">
              <div className={tabContent}>
                <pre className={code}>{`git clone https://github.com/JefferyHcool/BiliNote.git
cd OpenNoter
mv .env.example .env`}</pre>
                <pre className={code}>{`cd backend
pip install -r requirements.txt
python main.py`}</pre>
                <pre className={code}>{`cd OpenNoter_frontend
pnpm install
pnpm dev`}</pre>
                <p className={paragraph}>
                  访问 <code>http://localhost:3015</code>。
                </p>
              </div>
            </TabPanel>
            <TabPanel id="docker">
              <div className={tabContent}>
                <pre className={code}>{`git clone https://github.com/JefferyHcool/BiliNote.git
cd OpenNoter
mv .env.example .env
docker compose up --build`}</pre>
                <p className={paragraph}>端口在 .env 里配置：前端默认 3015，后端默认 8483。</p>
              </div>
            </TabPanel>
          </Tabs>
        </Panel>
      </Section>

      <Section title="联系和加入社区">
        <div className={community}>
          <Panel>
            <div className={qrContent}>
              <div className={qrPlaceholder}>
                <Text styles={qrPlaceholderText}>二维码占位</Text>
              </div>
              <h3 className={cardTitle}>OpenNoter 公众号</h3>
              <Text styles={cardText}>回复「交流群」获取二维码</Text>
            </div>
          </Panel>
        </div>
      </Section>

      <Section title="License">
        <p className={paragraph}>MIT License</p>
      </Section>

      <footer className={footer}>
        <Text styles={paragraph}>
          欢迎 <Link href="https://github.com/JefferyHcool/BiliNote/issues">提 issue</Link>、
          <Link href="https://github.com/JefferyHcool/BiliNote">Star</Link> 或 PR。
        </Text>
      </footer>
    </Page>
  )
}
