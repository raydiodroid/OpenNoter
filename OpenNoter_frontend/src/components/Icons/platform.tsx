import { siApple, siBilibili, siKuaishou, siTiktok, siYoutube, type SimpleIcon } from 'simple-icons'
import bcutLogo from '@/assets/brands/bcut-official.png'
import Folder from '@react-spectrum/s2/icons/Folder'

/** Paths come directly from Simple Icons; currentColor follows the light/dark theme. */
function BrandIcon({ icon, label = icon.title }: { icon: SimpleIcon; label?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="100%"
      height="100%"
      fill="currentColor"
      role="img"
      aria-label={label}
    >
      <path d={icon.path} />
    </svg>
  )
}

export const KuaishouLogo = () => <BrandIcon icon={siKuaishou} label="快手" />
export const DouyinLogo = () => <BrandIcon icon={siTiktok} label="抖音" />
export const BiliBiliLogo = () => <BrandIcon icon={siBilibili} label="哔哩哔哩" />
export const YoutubeLogo = () => <BrandIcon icon={siYoutube} />
export const AppleLogo = () => <BrandIcon icon={siApple} />
export const LocalLogo = () => <Folder aria-label="本地视频" />

// Unmodified logo from https://bcut.bilibili.cn/ (app.f3896fe6.js, module 178a).
// The official white/transparent artwork is used as a mask to follow the theme.
export const BcutLogo = () => (
  <span
    role="img"
    aria-label="必剪"
    style={{
      display: 'inline-block',
      width: '100%',
      height: '100%',
      backgroundColor: 'currentColor',
      mask: `url(${bcutLogo}) center / contain no-repeat`,
    }}
  />
)
