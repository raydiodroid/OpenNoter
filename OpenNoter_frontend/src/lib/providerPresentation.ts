import type { AsrProvider } from '@/services/transcriber'
import type { Provider } from '@/types'

/** Only the built-in endpoint has an automatic logo; custom providers keep their choice. */
export const hasFixedAsrIcon = (provider?: AsrProvider) =>
  Boolean(provider?.builtin && provider.engine === 'groq')

export function asrProviderIcon(provider: AsrProvider, baseUrl = provider.base_url): string {
  if (!hasFixedAsrIcon(provider)) return provider.logo
  try {
    const host = new URL(baseUrl).hostname.toLowerCase()
    if (host === 'api.groq.com') return 'groq'
    if (host === 'api.openai.com') return 'openai'
  } catch {
    // An incomplete address draft still uses the generic endpoint icon.
  }
  return 'server'
}

/** Keep the order within each group and never mutate the server's inventory. */
export function sortAsrProviders(providers: AsrProvider[]): AsrProvider[] {
  const rank = (p: AsrProvider) => {
    if (!p.builtin) return 3
    if (p.engine === 'fast-whisper' || p.engine === 'mlx-whisper') return 1
    if (p.engine === 'groq' && asrProviderIcon(p) === 'server') return 2
    return 0
  }
  return [...providers].sort((a, b) => rank(a) - rank(b))
}

export function sortModelProviders(providers: Provider[]): Provider[] {
  const rank = (p: Provider) =>
    p.type !== 'built-in' ? 2 : p.id.toLowerCase() === 'ollama' ? 1 : 0
  return [...providers].sort((a, b) => rank(a) - rank(b))
}
