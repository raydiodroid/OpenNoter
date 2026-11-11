export const PROVIDER_ICONS = [
  { id: 'custom', label: '统一网关' },
  { id: 'openai', label: 'OpenAI' },
  { id: 'claude', label: 'Claude' },
  { id: 'gemini', label: 'Gemini' },
  { id: 'google', label: 'Google' },
  { id: 'ollama', label: 'Ollama' },
  { id: 'deepseek', label: 'DeepSeek' },
  { id: 'qwen', label: 'Qwen' },
  { id: 'zhipu', label: '智谱' },
  { id: 'moonshot', label: 'Moonshot' },
  { id: 'groq', label: 'Groq' },
  { id: 'mistral', label: 'Mistral' },
  { id: 'meta', label: 'Meta' },
  { id: 'microsoft', label: 'Microsoft' },
] as const

const aliases: Record<string, string> = {
  anthropic: 'claude',
  tongyi: 'qwen',
  dashscope: 'qwen',
  glm: 'zhipu',
  kimi: 'moonshot',
  llama: 'meta',
  azure: 'microsoft',
}

/** Keep older saved icon IDs usable without duplicating their artwork in the picker. */
export function canonicalProviderIcon(value: string): string {
  const normalized = value.toLowerCase()
  return aliases[normalized] || normalized
}
