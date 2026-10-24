import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Heading, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import { Page, Panel } from '@/components/Page'

/**
 * 路由级错误边界。
 *
 * 背景：监控页曾经因为后端返回 `data: []` 而抛未捕获 TypeError，
 * 整棵 React 树被卸载 —— 连左侧导航一起白屏，用户只能手改 URL 才能离开。
 * React 的规则是：没有 ErrorBoundary 时，渲染期异常会冒泡到根并卸载全部内容。
 *
 * 这里把它收窄成「只有出错的那个页面变成错误态，侧栏和顶栏照常可用」，
 * 并提供「重试」按钮（重置 state 让子树重新挂载）。
 */
const wrap = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'start',
  gap: 12,
})

const detail = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  margin: 0,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
})

type Props = { children: ReactNode; resetKey?: string }
type State = { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 保留到控制台，方便用户反馈时贴日志
    console.error('[ErrorBoundary] 页面渲染失败:', error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    // 路由变化时自动复位，避免用户被永久困在错误页
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  private reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <Page>
        <Panel>
          <div className={wrap}>
            <Heading level={2}>这个页面出错了</Heading>
            <Text>
              页面渲染时抛出了未捕获异常，已阻止它影响其它区域。可以尝试重试；若持续失败，请把下面的信息反馈给管理员。
            </Text>
            <Text styles={detail}>{`${error.name}: ${error.message}`}</Text>
            <Button variant="secondary" onPress={this.reset}>
              重试
            </Button>
          </div>
        </Panel>
      </Page>
    )
  }
}
