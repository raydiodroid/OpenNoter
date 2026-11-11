import type { AsrProvider } from '@/services/transcriber'
import type { Provider } from '@/types'

/** Sorting follows the endpoint, independently of the administrator's chosen icon. */
function isHostedAsrEndpoint(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase()
    return host === 'api.groq.com' || host === 'api.openai.com'
  } catch {
    return false
  }
}

/** Keep the order within each group and never mutate the server's inventory. */
export function sortAsrProviders(providers: AsrProvider[]): AsrProvider[] {
  const rank = (p: AsrProvider) => {
    if (!p.builtin) return 3
    if (p.engine === 'fast-whisper' || p.engine === 'mlx-whisper') return 1
    if (p.engine === 'groq' && !isHostedAsrEndpoint(p.base_url)) return 2
    return 0
  }
  return [...providers].sort((a, b) => rank(a) - rank(b))
}

export function sortModelProviders(providers: Provider[]): Provider[] {
  const rank = (p: Provider) =>
    p.type !== 'built-in' ? 2 : p.id.toLowerCase() === 'ollama' ? 1 : 0
  return [...providers].sort((a, b) => rank(a) - rank(b))
}
