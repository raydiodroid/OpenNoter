import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Page, PageHeader } from '@/components/Page'
import { ByokPolicyPanel } from './ByokSettings'

const stack = style({ display: 'flex', flexDirection: 'column', gap: 24 })

/**
 * 站点设置页（仅管理员可见）。
 *
 * ## 为什么单独一页
 *
 * 这里放的是**站点的部署策略**，不属于任何一个功能域：
 *
 * - 模型凭据策略 → 影响「花谁的钱」，但它是运营决策，不是模型本身
 * - 平台 Cookie 策略 → 影响「用谁的身份」，横跨 B 站/YouTube/抖音/快手
 *
 * 之前它只挂在「模型配置」页里，于是出现两个问题：
 *  1. 管理员在模型页关掉个人凭据，**下载页的个人 Cookie 也会无声失效**，
 *     因为两者共用一个 `mode`——而管理员根本不知道自己在改 Cookie；
 *  2. 它叫「模型凭据来源」，但语义其实是全站策略，位置与命名都在说谎。
 *
 * 现在拆成两个独立开关，并挪到这个独立页面：模型页只管模型，下载页只管
 * 下载，策略集中一处。
 */
export default function SiteSettingsPage() {
  return (
    <Page>
      <PageHeader
        title="站点设置"
        description="全站策略：用户能否自带模型凭据与平台 Cookie。修改后立即生效，无需重启。"
      />
      <div className={stack}>
        <ByokPolicyPanel />
      </div>
    </Page>
  )
}
