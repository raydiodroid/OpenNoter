import type { ComponentType } from 'react'
import {
  BiliBiliLogo,
  DouyinLogo,
  KuaishouLogo,
  LocalLogo,
  YoutubeLogo,
} from '@/components/Icons/platform'

export interface PlatformOption {
  value: string
  label: string
  Logo: ComponentType
}

export const PLATFORMS: PlatformOption[] = [
  { value: 'bilibili', label: '哔哩哔哩', Logo: BiliBiliLogo },
  { value: 'youtube', label: 'YouTube', Logo: YoutubeLogo },
  { value: 'douyin', label: '抖音', Logo: DouyinLogo },
  { value: 'kuaishou', label: '快手', Logo: KuaishouLogo },
  { value: 'local', label: '本地视频', Logo: LocalLogo },
]

export const DOWNLOAD_PLATFORMS = PLATFORMS.filter(p => p.value !== 'local')

export const PLATFORM_LABEL: Record<string, string> = Object.fromEntries(
  PLATFORMS.map(p => [p.value, p.label]),
)

export interface Choice {
  value: string
  label: string
}

export const NOTE_STYLES: Choice[] = [
  { value: 'minimal', label: '精简' },
  { value: 'detailed', label: '详细' },
  { value: 'tutorial', label: '教程' },
  { value: 'academic', label: '学术' },
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'life_journal', label: '生活向' },
  { value: 'task_oriented', label: '任务导向' },
  { value: 'business', label: '商业风格' },
  { value: 'meeting_minutes', label: '会议纪要' },
]

export const NOTE_FORMATS: Choice[] = [
  { value: 'toc', label: '目录' },
  { value: 'link', label: '原片跳转' },
  { value: 'screenshot', label: '原片截图' },
  { value: 'summary', label: 'AI 总结' },
]

export const QUALITY: Choice[] = [
  { value: 'fast', label: '快速（省流量）' },
  { value: 'medium', label: '标准' },
  { value: 'slow', label: '高质量（最慢）' },
]

export const TASK_STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  PARSING: '解析链接',
  DOWNLOADING: '下载音频',
  TRANSCRIBING: '转写文字',
  SUMMARIZING: '总结内容',
  FORMATTING: '排版整理',
  SAVING: '保存完成',
  SUCCESS: '已完成',
  FAILED: '失败',
}

export const PROGRESS_STEPS = [
  { key: 'PARSING', label: '解析链接' },
  { key: 'DOWNLOADING', label: '下载音频' },
  { key: 'TRANSCRIBING', label: '转写文字' },
  { key: 'SUMMARIZING', label: '总结内容' },
  { key: 'SAVING', label: '保存完成' },
] as const
