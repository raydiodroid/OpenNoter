import { useEffect, useState } from 'react'
import { Tab, TabList, TabPanel, Tabs } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Page, PageHeader } from '@/components/Page'
import ModelDefaults from './ModelDefaults'
import ModelPage from './ModelPage'
import TranscriberPage from './TranscriberPage'
import { MyCredentialsSection } from './MyCredentials'

const panelBody = style({ paddingTop: 16 })
/**
 * 同一 Tab 内多个 Section 的纵向堆叠。
 *
 * `Section` 自身不含外边距（它是语义分组，不是卡片），所以多个 Section
 * 直接并列会**贴着彼此**。这里统一给 24px 间距。 */
const sectionStack = style({ display: 'flex', flexDirection: 'column', gap: 24 })

type TabKey = 'model' | 'transcriber'

/**
 * AI 模型 + 音频转写合并页。
 * 两个设置域都属于「模型层」，放同一页用 Tabs 切换，侧栏只留一个一级入口。
 */
export default function AISettingsPage({
  tab: initialTab = 'model',
  isCreate = false,
}: {
  tab?: TabKey
  isCreate?: boolean
}) {
  const [tab, setTab] = useState<TabKey>(initialTab)

  useEffect(() => {
    setTab(initialTab)
  }, [initialTab])

  return (
    <Page>
      <PageHeader
        title="模型配置"
        description="配置生成笔记用的大模型，以及把视频音频转成文字的引擎。"
      />

      <Tabs
        aria-label="模型与转写"
        selectedKey={tab}
        onSelectionChange={key => setTab(String(key) as TabKey)}
      >
        <TabList aria-label="模型与转写">
          <Tab id="model">AI 模型</Tab>
          <Tab id="transcriber">音频转写</Tab>
        </TabList>

        <TabPanel id="model" shouldForceMount>
          <div className={panelBody}>
            {/* 个人凭据区块（未开启 BYOK 时显示「当前站点统一使用管理员配置」）。
                站点级策略在「设置 → 站点设置」页，不在这里——
                那是全站决策，不属于模型域。 */}
            <div className={sectionStack}>
              <ModelDefaults />
              <MyCredentialsSection />
              <ModelPage embedded isCreate={isCreate} />
            </div>
          </div>
        </TabPanel>

        <TabPanel id="transcriber" shouldForceMount>
          <div className={panelBody}>
            <TranscriberPage embedded />
          </div>
        </TabPanel>
      </Tabs>
    </Page>
  )
}
