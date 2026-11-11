import Cloud from '@react-spectrum/s2/icons/Cloud'
import Server from '@react-spectrum/s2/icons/Data'
import Audio from '@react-spectrum/s2/icons/AudioWave'
import Computer from '@react-spectrum/s2/icons/DeviceDesktop'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import { ProviderMark, PlatformMark } from '@/components/Brand'
import { AppleLogo, BcutLogo, KuaishouLogo } from '@/components/Icons/platform'

const icons = { cloud: Cloud, server: Server, audio: Audio, whisper: Computer }
const brands = { apple: AppleLogo, bcut: BcutLogo, kuaishou: KuaishouLogo }
const box = style({
  display: 'inline-flex',
  flexShrink: 0,
  alignItems: 'center',
  justifyContent: 'center',
  width: 32,
  height: 32,
  borderRadius: 'default',
  color: 'neutral',
  backgroundColor: 'base',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
})
export function AsrMark({ name }: { name: string }) {
  const Icon = icons[name as keyof typeof icons] || Cloud
  const Brand = brands[name as keyof typeof brands]
  return (
    <span className={box}>
      {name === 'openai' || name === 'groq' ? (
        <ProviderMark name={name} size={20} boxed={false} />
      ) : Brand ? (
        <PlatformMark Logo={Brand} size={20} />
      ) : (
        <Icon />
      )}
    </span>
  )
}
