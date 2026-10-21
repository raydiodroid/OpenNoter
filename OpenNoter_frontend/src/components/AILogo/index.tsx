/**
 * AI 厂商图标。
 *
 * 只从 @lobehub/icons 的深层路径按需引入白名单图标：
 * - 包根 index 会 re-export 全部厂商，其中个别条目本身有打包错误（如 Xuanyuan 缺 components/Avatar），
 *   走根导入会让 Rollup 直接报错，也会把整个图标库拖进产物。
 * - 深层导入保证 tree-shake，产物只包含用到的 13 个。
 *
 * 视觉策略：保留品牌色，统一放进同尺寸圆角方框，与 S2 的 20px 线性图标视觉重量对齐。
 */
import OpenAI from "@lobehub/icons/es/OpenAI/components/Mono"
import Claude from "@lobehub/icons/es/Claude/components/Mono"
import Gemini from "@lobehub/icons/es/Gemini/components/Mono"
import Ollama from "@lobehub/icons/es/Ollama/components/Mono"
import DeepSeek from "@lobehub/icons/es/DeepSeek/components/Mono"
import Qwen from "@lobehub/icons/es/Qwen/components/Mono"
import Zhipu from "@lobehub/icons/es/Zhipu/components/Mono"
import Moonshot from "@lobehub/icons/es/Moonshot/components/Mono"
import Groq from "@lobehub/icons/es/Groq/components/Mono"
import Mistral from "@lobehub/icons/es/Mistral/components/Mono"
import Meta from "@lobehub/icons/es/Meta/components/Mono"
import Microsoft from "@lobehub/icons/es/Microsoft/components/Mono"
import Google from "@lobehub/icons/es/Google/components/Mono"

import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

import CustomLogo from '@/assets/customAI.png'

/** 供应商 logo 的 key 全部小写，provider.logo 在数据库里也是小写 */
const ICON_MAP: Record<string, any> = {
  openai: OpenAI,
  claude: Claude,
  anthropic: Claude,
  gemini: Gemini,
  google: Google,
  ollama: Ollama,
  deepseek: DeepSeek,
  qwen: Qwen,
  tongyi: Qwen,
  dashscope: Qwen,
  zhipu: Zhipu,
  glm: Zhipu,
  moonshot: Moonshot,
  kimi: Moonshot,
  groq: Groq,
  mistral: Mistral,
  meta: Meta,
  llama: Meta,
  microsoft: Microsoft,
  azure: Microsoft,
}

const box = style({
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  borderRadius: 'default',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  backgroundColor: 'base',
  color: 'neutral',
})

interface AILogoProps {
  name: string
  size?: number
  /** 是否套统一容器（默认开） */
  boxed?: boolean
  className?: string
}

const AILogo = ({ name, size = 20, boxed = true, className }: AILogoProps) => {
  const Icon = name ? ICON_MAP[name.toLowerCase()] : undefined

  const inner = Icon ? (
    <Icon size={size} />
  ) : (
    <img src={CustomLogo} alt={name || 'logo'} style={{ width: size, height: size }} />
  )

  if (!boxed) return inner

  return (
    <span className={`${box}${className ? ` ${className}` : ''}`} style={{ width: size + 12, height: size + 12 }}>
      {inner}
    </span>
  )
}

export default AILogo
