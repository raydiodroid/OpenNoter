import { API_BASE } from './urls'

let scope: string | null = null
let epoch = 0
const listeners = new Set<() => void>()

/** Identity changes invalidate both cached state and in-flight responses. */
export function setSessionIdentity(identity: number | 'anonymous' | null) {
  const next = identity === null ? null : `${API_BASE}:${identity}`
  if (scope === next) return
  scope = next
  epoch++
  for (const listener of listeners) listener()
}
export const sessionEpoch = () => epoch
export const sessionScope = () => scope
export const onSessionChange = (listener: () => void) => {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
