import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ActionButton, Button, ProgressCircle, Text, TextArea } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import CloseIcon from '@react-spectrum/s2/icons/Close'
import DeleteIcon from '@react-spectrum/s2/icons/Delete'
import FullScreenIcon from '@react-spectrum/s2/icons/FullScreen'
import FullScreenExitIcon from '@react-spectrum/s2/icons/FullScreenExit'
import ArrowUpIcon from '@react-spectrum/s2/icons/ArrowUpSend'
import BookIcon from '@react-spectrum/s2/icons/FileText'

import { useChatStore } from '@/store/chatStore'
import { useTaskStore } from '@/store/taskStore'
import {
  askQuestion,
  getChatStatus,
  indexTask,
  type ChatSource,
  type IndexStatus,
} from '@/services/chat'
import { toast } from '@/lib/toast'

const pane = style({
  display: 'flex',
  flexDirection: 'column',
  height: 'full',
  minHeight: 0,
  backgroundColor: 'base',
  overflow: 'hidden',
})

const header = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  height: 48,
  paddingX: 16,
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 1,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const headerTitle = style({ font: 'title-sm', color: 'neutral', margin: 0 })

const body = style({
  flexGrow: 1,
  minHeight: 0,
  overflowY: 'auto',
  paddingX: 16,
  paddingY: 16,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
})

const messageRow = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  maxWidth: '92%',
})

const messageRowUser = style({ alignSelf: 'end', alignItems: 'end' })

const bubbleBase = style({
  paddingX: 12,
  paddingY: 8,
  borderRadius: 'lg',
  font: 'body-sm',
})

const bubbleUser = style({
  backgroundColor: 'accent-900',
  color: 'white',
})

const bubbleAssistant = style({
  backgroundColor: 'layer-1',
  color: 'neutral',
})

const sourceList = style({
  display: 'flex',
  flexWrap: 'wrap',
  gap: 4,
  marginTop: 4,
})

const sourceTag = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  backgroundColor: 'layer-2',
  borderRadius: 'sm',
  paddingX: 4,
  paddingY: 2,
})

const composer = style({
  display: 'flex',
  alignItems: 'end',
  gap: 8,
  padding: 12,
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 1,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const centered = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  height: 'full',
  padding: 24,
  textAlign: 'center',
})

const hint = style({ font: 'detail', color: 'neutral-subdued' })

const headerActions = style({ display: 'flex', alignItems: 'center', gap: 2 })
const userContent = style({ whiteSpace: 'pre-wrap' })
const loadingRow = style({ display: 'flex', alignItems: 'center', gap: 8 })
const composerField = style({ flex: 1, minWidth: 0 })

function Sources({ sources }: { sources: ChatSource[] }) {
  const [open, setOpen] = useState(false)
  if (!sources.length) return null

  return (
    <div>
      <ActionButton isQuiet size="S" onPress={() => setOpen(value => !value)}>
        <BookIcon />
        <Text>引用来源（{sources.length}）</Text>
      </ActionButton>
      {open ? (
        <div className={sourceList}>
          {sources.map((source, index) => (
            <span key={index} className={sourceTag}>
              {source.source_type === 'markdown'
                ? source.section_title || '笔记'
                : `${Math.round(source.start_time ?? 0)}s–${Math.round(source.end_time ?? 0)}s`}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export default function ChatPane({
  taskId,
  mode,
  onModeChange,
  onClose,
}: {
  taskId: string
  mode: 'half' | 'full'
  onModeChange: (mode: 'half' | 'full') => void
  onClose: () => void
}) {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null)

  const storedMessages = useChatStore(state => state.chatHistory[taskId])
  const messages = useMemo(() => storedMessages ?? [], [storedMessages])
  const addMessage = useChatStore(state => state.addMessage)
  const clearChat = useChatStore(state => state.clearChat)

  const currentTask = useTaskStore(
    state => state.tasks.find(t => t.id === state.currentTaskId) ?? null
  )

  const startIndex = useCallback(async () => {
    setIndexStatus('indexing')
    try {
      await indexTask(taskId)
    } catch {
      setIndexStatus('failed')
    }
  }, [taskId])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const res = await getChatStatus(taskId)
        if (cancelled) return
        setIndexStatus(res.status)
        if (res.status === 'idle') {
          await indexTask(taskId)
          if (!cancelled) setIndexStatus('indexing')
        }
        if (res.status === 'indexing' || res.status === 'idle') {
          timer = setTimeout(poll, 2000)
        }
      } catch {
        if (!cancelled) setIndexStatus('failed')
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId])

  const modelConfig = useMemo(
    () => ({
      providerId: currentTask?.formData?.provider_id,
      modelName: currentTask?.formData?.model_name,
    }),
    [currentTask?.formData?.provider_id, currentTask?.formData?.model_name]
  )

  const send = useCallback(async () => {
    const question = input.trim()
    if (!question || loading) return
    if (!modelConfig.providerId || !modelConfig.modelName) {
      toast.error('无法获取模型配置，请确认任务已完成')
      return
    }

    addMessage(taskId, { role: 'user', content: question })
    setInput('')
    setLoading(true)
    try {
      const history = messages.map(message => ({ role: message.role, content: message.content }))
      const res = await askQuestion({
        task_id: taskId,
        question,
        history,
        provider_id: modelConfig.providerId,
        model_name: modelConfig.modelName,
      })
      addMessage(taskId, { role: 'assistant', content: res.answer, sources: res.sources })
    } catch {
      toast.error('问答请求失败')
    } finally {
      setLoading(false)
    }
  }, [addMessage, input, loading, messages, modelConfig, taskId])

  if (indexStatus === null || indexStatus === 'idle' || indexStatus === 'indexing') {
    return (
      <div className={pane}>
        <div className={header}>
          <h2 className={headerTitle}>AI 问答</h2>
          <ActionButton isQuiet size="S" onPress={onClose} aria-label="关闭">
            <CloseIcon />
          </ActionButton>
        </div>
        <div className={centered}>
          <ProgressCircle aria-label="索引中" isIndeterminate />
          <Text styles={hint}>正在索引笔记内容…</Text>
          <Text styles={hint}>首次使用需要下载 Embedding 模型（约 80MB）</Text>
        </div>
      </div>
    )
  }

  if (indexStatus === 'failed') {
    return (
      <div className={pane}>
        <div className={header}>
          <h2 className={headerTitle}>AI 问答</h2>
          <ActionButton isQuiet size="S" onPress={onClose} aria-label="关闭">
            <CloseIcon />
          </ActionButton>
        </div>
        <div className={centered}>
          <Text styles={hint}>索引失败，请重试</Text>
          <Button variant="secondary" onPress={startIndex}>
            重新索引
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className={pane}>
      <div className={header}>
        <h2 className={headerTitle}>AI 问答</h2>
        <div className={headerActions}>
          <ActionButton
            isQuiet
            size="S"
            aria-label={mode === 'half' ? '全屏' : '半屏'}
            onPress={() => onModeChange(mode === 'half' ? 'full' : 'half')}
          >
            {mode === 'half' ? <FullScreenIcon /> : <FullScreenExitIcon />}
          </ActionButton>
          {messages.length > 0 ? (
            <ActionButton isQuiet size="S" aria-label="清空对话" onPress={() => clearChat(taskId)}>
              <DeleteIcon />
            </ActionButton>
          ) : null}
          <ActionButton isQuiet size="S" aria-label="关闭" onPress={onClose}>
            <CloseIcon />
          </ActionButton>
        </div>
      </div>

      <div className={body}>
        {messages.length === 0 && !loading ? (
          <div className={centered}>
            <Text styles={hint}>针对笔记内容提问</Text>
            <Text styles={hint}>例如：这个视频的核心观点是什么？</Text>
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              key={index}
              className={message.role === 'user' ? `${messageRow} ${messageRowUser}` : messageRow}
            >
              <div
                className={
                  message.role === 'user'
                    ? `${bubbleBase} ${bubbleUser}`
                    : `${bubbleBase} ${bubbleAssistant}`
                }
              >
                {message.role === 'user' ? (
                  <span className={userContent}>{message.content}</span>
                ) : (
                  <div className="note-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                  </div>
                )}
              </div>
              {message.role === 'assistant' && message.sources ? (
                <Sources sources={message.sources} />
              ) : null}
            </div>
          ))
        )}
        {loading ? (
          <div className={loadingRow}>
            <ProgressCircle aria-label="思考中" isIndeterminate size="S" />
            <Text styles={hint}>思考中…</Text>
          </div>
        ) : null}
      </div>

      <div className={composer}>
        <div className={composerField}>
          <TextArea
            aria-label="输入你的问题"
            value={input}
            onChange={setInput}
            placeholder="输入你的问题，Enter 发送"
            isDisabled={loading}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send()
              }
            }}
          />
        </div>
        <ActionButton aria-label="发送" isDisabled={!input.trim() || loading} onPress={send}>
          <ArrowUpIcon />
        </ActionButton>
      </div>
    </div>
  )
}
