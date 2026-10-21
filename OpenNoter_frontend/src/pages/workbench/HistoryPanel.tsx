import { useMemo, useState } from 'react'
import {
  ActionButton,
  AlertDialog,
  Content,
  DialogTrigger,
  Heading,
  IllustratedMessage,
  SearchField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import DeleteIcon from '@react-spectrum/s2/icons/Delete'
import MagicWand from '@react-spectrum/s2/icons/MagicWand'
import NoSearchResults from '@react-spectrum/s2/illustrations/linear/NoSearchResults'
import AIGenerate from '@react-spectrum/s2/illustrations/linear/AIGenerate'
import Fuse from 'fuse.js'
import PinyinMatch from 'pinyin-match'

import { TASK_STATUS_LABEL } from '@/constants'
import { formatDateTime } from '@/lib/format'
import { imageProxy } from '@/services/note'
import { useTaskStore } from '@/store/taskStore'
import {
  ListRow,
  ListRowBody,
  ListRowButton,
  ListRowMetaRow,
  ListRowTitle,
} from '@/components/ListRow'
import { StatusIndicator } from '@/components/StatusIndicator'
import type { Task } from '@/types'

const wrap = style({
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  height: 'full',
})

const searchRow = style({
  paddingX: 16,
  paddingY: 12,
  flexShrink: 0,
})

const list = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  paddingX: 12,
  paddingBottom: 24,
  overflowY: 'auto',
  minHeight: 0,
})

const thumb = style({
  width: 64,
  height: 36,
  borderRadius: 'sm',
  flexShrink: 0,
  objectFit: 'cover',
  backgroundColor: 'gray-200',
})

const thumbFallback = style({
  width: 64,
  height: 36,
  borderRadius: 'sm',
  flexShrink: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: 'gray-100',
  color: 'neutral-subdued',
})

const empty = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexGrow: 1,
  padding: 24,
})

/** 搜索框铺满一行 */
const fullWidth = style({ width: 'full' })

const statusTone = (status: Task['status']) => {
  if (status === 'SUCCESS') return 'positive'
  if (status === 'FAILED') return 'negative'
  return 'notice'
}

/** 参数面板里的历史列表：选中即回到阅读器，删除走二次确认 */
export default function HistoryPanel({ onOpen }: { onOpen: () => void }) {
  const tasks = useTaskStore(state => state.tasks)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const removeTask = useTaskStore(state => state.removeTask)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)

  const [query, setQuery] = useState('')

  const fuse = useMemo(
    () => new Fuse(tasks, { keys: ['audioMeta.title'], threshold: 0.4 }),
    [tasks]
  )

  const filtered = useMemo(() => {
    const keyword = query.trim()
    if (!keyword) return tasks

    const fuzzy = fuse.search(keyword).map(result => result.item)
    const pinyin = tasks.filter(task => {
      const name = task.audioMeta.title || ''
      return (PinyinMatch as any)?.match?.(name, keyword)
    })

    const merged = new Map<string, Task>()
    for (const task of [...fuzzy, ...pinyin]) merged.set(task.id, task)
    return Array.from(merged.values())
  }, [fuse, query, tasks])

  const open = (task: Task) => {
    setCurrentTask(task.id)
    onOpen()
  }

  return (
    <div className={wrap}>
      <div className={searchRow}>
        <SearchField
          aria-label="搜索笔记标题"
          placeholder="搜索标题，支持拼音…"
          value={query}
          onChange={setQuery}
          styles={fullWidth}
        />
      </div>

      {filtered.length === 0 ? (
        <div className={empty}>
          <IllustratedMessage size="S">
            {query ? <NoSearchResults /> : <AIGenerate />}
            <Heading>{query ? '没有匹配的笔记' : '还没有生成过笔记'}</Heading>
            <Content>
              {query
                ? '换个关键词试试，支持标题模糊匹配与拼音。'
                : '切回「生成笔记」，粘贴一个视频链接开始。'}
            </Content>
          </IllustratedMessage>
        </div>
      ) : (
        <div className={list}>
          {filtered.map(task => {
            const label = task.audioMeta.title || '未命名笔记'
            return (
              <ListRow key={task.id} selected={task.id === currentTaskId}>
                <ListRowButton onClick={() => open(task)}>
                  {task.audioMeta.cover_url ? (
                    <img
                      src={imageProxy(task.audioMeta.cover_url)}
                      alt=""
                      referrerPolicy="no-referrer"
                      className={thumb}
                    />
                  ) : (
                    <span className={thumbFallback}>
                      <MagicWand />
                    </span>
                  )}

                  <ListRowBody>
                    <ListRowTitle title={label}>{label}</ListRowTitle>
                    <ListRowMetaRow>
                      <StatusIndicator tone={statusTone(task.status)}>
                        {TASK_STATUS_LABEL[task.status] || task.status}
                      </StatusIndicator>
                      {formatDateTime(task.createdAt)}
                    </ListRowMetaRow>
                  </ListRowBody>
                </ListRowButton>

                <DialogTrigger>
                  <ActionButton isQuiet size="S" aria-label={`删除 ${label}`}>
                    <DeleteIcon />
                  </ActionButton>
                  <AlertDialog
                    title="删除这条笔记？"
                    variant="destructive"
                    primaryActionLabel="删除"
                    cancelLabel="取消"
                    onPrimaryAction={() => removeTask(task.id)}
                  >
                    本地记录与后端生成产物都会被删除，此操作不可撤销。
                  </AlertDialog>
                </DialogTrigger>
              </ListRow>
            )
          })}
        </div>
      )}
    </div>
  )
}
