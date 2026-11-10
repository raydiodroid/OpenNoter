import { ProviderMark } from '@/components/Brand'
import { ASR_ICONS } from '@/lib/asrIcons'

export function AsrMark({ name }: { name: string }) {
  if (name === 'openai' || name === 'groq') return <ProviderMark name={name} size={20} />
  const icon = ASR_ICONS.find(icon => icon.id === name) || ASR_ICONS[0]
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        flexShrink: 0,
        alignItems: 'center',
        justifyContent: 'center',
        width: 32,
        height: 32,
        borderRadius: 6,
        color: icon.color,
        background: `${icon.color}12`,
        border: `1px solid ${icon.color}30`,
      }}
    >
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d={icon.path} />
      </svg>
    </span>
  )
}
