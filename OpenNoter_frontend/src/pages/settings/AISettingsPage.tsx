import { useEffect, useState } from 'react'
import { Tab, TabList, TabPanel, Tabs } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Page, PageHeader } from '@/components/Page'
import ModelPage from './ModelPage'
import TranscriberPage from './TranscriberPage'
import { ByokModePanel } from './ByokSettings'
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

        <TabPanel id="model">
          <div className={panelBody}>
            {/* BYOK 分两层，放在同一页里：
                - 站点默认由管理员设（ByokModePanel，仅管理员可见）
                - 用户自己的凭据（MyCredentialsSection，人人可见，未开启 BYOK 时
                  显示「当前站点统一使用管理员配置」）
                原先「我的凭据」是独立的一级设置页，用户要在三个页面之间找。 */}
            <div className={sectionStack}>
              <MyCredentialsSection />
              <ByokModePanel />
              <ModelPage embedded isCreate={isCreate} />
            </div>
          </div>
        </TabPanel>

        <TabPanel id="transcriber">
          <div className={panelBody}>
            <TranscriberPage embedded />
          </div>
        </TabPanel>
      </Tabs>
    </Page>
  )
}
