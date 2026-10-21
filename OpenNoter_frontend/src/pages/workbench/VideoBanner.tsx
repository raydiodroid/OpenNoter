import { Link, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import OpenInIcon from '@react-spectrum/s2/icons/OpenIn'
import { NOTE_STYLES, PLATFORM_LABEL } from '@/constants'
import { formatDateTime } from '@/lib/format'
import { imageProxy } from '@/services/note'
import type { AudioMeta } from '@/types'

const banner = style({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: 16,
  padding: 12,
  marginBottom: 24,
  borderRadius: 'lg',
  backgroundColor: 'base',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderWidth: 1,
})

const cover = style({
  width: 144,
  height: 81,
  flexShrink: 0,
  objectFit: 'cover',
  borderRadius: 'sm',
  backgroundColor: 'gray-200',
})

const info = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  minWidth: 0,
  flexGrow: 1,
})

const title = style({
  font: 'title-sm',
  color: 'neutral',
  truncate: true,
})

const meta = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: 8,
})


export default function VideoBanner({
  audioMeta,
  videoUrl,
  modelName,
  noteStyle,
  createdAt,
}: {
  audioMeta?: AudioMeta
  videoUrl?: string
  modelName?: string
  noteStyle?: string
  createdAt?: string
}) {
  if (!audioMeta) return null

  const coverUrl = audioMeta.cover_url ? imageProxy(audioMeta.cover_url) : ''
  const uploader = audioMeta.raw_info?.uploader || ''
  const platform = PLATFORM_LABEL[audioMeta.platform] || audioMeta.platform || ''
  const originalUrl = videoUrl || audioMeta.raw_info?.webpage_url || ''
  const styleLabel = NOTE_STYLES.find(item => item.value === noteStyle)?.label || noteStyle || ''

  return (
    <div className={banner}>
      {coverUrl ? (
        <img src={coverUrl} alt="" referrerPolicy="no-referrer" className={cover} />
      ) : null}
      <div className={info}>
        <span className={title} title={audioMeta.title}>
          {audioMeta.title || '未命名视频'}
        </span>
        <span className={meta}>
          {uploader ? <Text>{uploader}</Text> : null}
          {uploader && platform ? <span>·</span> : null}
          {platform ? <Text>{platform}</Text> : null}
          {modelName ? <span>·</span> : null}
          {modelName ? <Text>{modelName}</Text> : null}
          {styleLabel ? <span>·</span> : null}
          {styleLabel ? <Text>{styleLabel}</Text> : null}
          {createdAt ? <span>·</span> : null}
          {createdAt ? <Text>{formatDateTime(createdAt)}</Text> : null}
          {originalUrl ? (
            <Link href={originalUrl} target="_blank">
              <OpenInIcon />
              <Text>原视频</Text>
            </Link>
          ) : null}
        </span>
      </div>
    </div>
  )
}
