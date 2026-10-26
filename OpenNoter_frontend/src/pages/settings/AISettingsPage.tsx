import { useEffect, useState } from 'react'
import { Tab, TabList, TabPanel, Tabs } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Page, PageHeader } from '@/components/Page'
import ModelPage from './ModelPage'
import TranscriberPage from './TranscriberPage'
import { ByokModePanel, MyCredentialsPanel } from './ByokSettings'

const panelBody = style({ paddingTop: 16 })

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
            {/* BYOK：用户自己的凭据（shared 模式下组件内部返回 null） */}
            <MyCredentialsPanel />
            {/* BYOK：站点模式切换（仅管理员可见，组件内部判定） */}
            <ByokModePanel />
            <ModelPage embedded isCreate={isCreate} />
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
