import Cloud from '@react-spectrum/s2/icons/Cloud'
import Server from '@react-spectrum/s2/icons/Data'
import Audio from '@react-spectrum/s2/icons/AudioWave'
import Computer from '@react-spectrum/s2/icons/DeviceDesktop'
import Laptop from '@react-spectrum/s2/icons/DeviceLaptop'
import Cut from '@react-spectrum/s2/icons/Cut'
import Video from '@react-spectrum/s2/icons/Video'
import { ProviderMark } from '@/components/Brand'
import { ASR_ICONS } from '@/lib/asrIcons'

const icons = {
  cloud: Cloud,
  server: Server,
  audio: Audio,
  whisper: Computer,
  apple: Laptop,
  bcut: Cut,
  kuaishou: Video,
}
export function AsrMark({ name }: { name: string }) {
  if (name === 'openai' || name === 'groq') return <ProviderMark name={name} size={20} />
  const icon = ASR_ICONS.find(icon => icon.id === name) || ASR_ICONS[0]
  const Icon = icons[name as keyof typeof icons] || Cloud
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
      <Icon />
    </span>
  )
}
