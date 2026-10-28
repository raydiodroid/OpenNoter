import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
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
import { Fold } from '@/components/Fold'

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
  const sourceListId = useId()
  if (!sources.length) return null

  return (
    <div>
      {/* 这里是「用户主动点击展开」，不是系统驱动，所以要给出 aria-expanded /
          aria-controls，让读屏用户知道这是可展开的、以及展开的内容在哪。 */}
      <ActionButton
        isQuiet
        size="S"
        aria-expanded={open}
        aria-controls={sourceListId}
        onPress={() => setOpen(value => !value)}
      >
        <BookIcon />
        <Text>引用来源（{sources.length}）</Text>
      </ActionButton>
      <Fold open={open}>
        <div className={sourceList} id={sourceListId}>
          {sources.map((source, index) => (
            <span
              key={`${source.source_type}-${index}`}
              className={sourceTag}
              title={source.text || undefined}
            >
              {source.source_type === 'markdown'
                ? source.section_title || '笔记'
                : source.source_type === 'meta'
                  // meta 是「视频信息」chunk（标题/作者/简介），没有时间轴。
                  // 历史实现落到 else 分支，渲染成无意义的「0s–0s」。
                  ? '视频信息'
                  : `${Math.round(source.start_time ?? 0)}s–${Math.round(source.end_time ?? 0)}s`}
            </span>
          ))}
        </div>
      </Fold>
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

  /**
   * 轮询代次。每次 +1 会让下面的 useEffect 重跑 —— 这是「重新索引」按钮
   * 能从 failed 态恢复的唯一机制。
   *
   * 为什么需要它：轮询链在拿到 failed（或连续网络错误）后就 return 了，
   * 不再 setTimeout。历史实现的 `startIndex` 只 setIndexStatus('indexing')
   * 然后 await indexTask()，但**没有任何代码再把它改回 indexed/failed** ——
   * 于是用户点「重新索引」后永久停在「正在索引笔记内容…」，只能关面板重开。
   */
  const [pollGeneration, setPollGeneration] = useState(0)

  const startIndex = useCallback(async () => {
    setIndexStatus('indexing')
    try {
      await indexTask(taskId)
      // 关键：重启轮询链，让后端把真实状态回传
      setPollGeneration(generation => generation + 1)
    } catch {
      setIndexStatus('failed')
    }
  }, [taskId])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let consecutiveErrors = 0

    const poll = async () => {
      try {
        const res = await getChatStatus(taskId)
        if (cancelled) return
        consecutiveErrors = 0
        setIndexStatus(res.status)
        if (res.status === 'idle') {
          await indexTask(taskId)
          if (!cancelled) setIndexStatus('indexing')
        }
        if (res.status === 'indexing' || res.status === 'idle') {
          // 退避：索引可能是首次下载 ~80MB embedding 模型，2s 固定间隔会白打几十次
          timer = setTimeout(poll, 2000)
        }
      } catch {
        if (cancelled) return
        consecutiveErrors += 1
        // 单次网络抖动（后端重启窗口、断网 1 秒）不该判死：退避重试 3 次后
        // 才显示 failed。历史实现一次 catch 就永久停止轮询，把「短暂不可用」
        // 升级成「必须手点重试」，而重试当时又是死锁的（见 startIndex 注释）。
        if (consecutiveErrors < 3) {
          timer = setTimeout(poll, Math.min(1000 * 2 ** consecutiveErrors, 8000))
        } else {
          setIndexStatus('failed')
        }
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // pollGeneration 递增会重跑本 effect —— 这是「重新索引」按钮恢复轮询的机制
  }, [taskId, pollGeneration])

  /**
   * 消息区自动滚到底部。
   *
   * 只在「用户本来就在底部附近」时才滚：否则用户往上翻历史时会被新消息
   * 强行拽回底部（这是聊天界面常见的反模式）。
   */
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const stickToBottomRef = useRef(true)

  useEffect(() => {
    const el = bodyRef.current
    if (!el || !stickToBottomRef.current) return
    el.scrollTop = el.scrollHeight
  }, [messages.length, loading])

  const handleBodyScroll = useCallback(() => {
    const el = bodyRef.current
    if (!el) return
    // 距底部 64px 以内视为「在底部」
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64
  }, [])

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
    // 不再因为「任务没记模型」而阻断发送。
    //
    // 历史实现会直接拒绝，理由是「这条笔记缺少模型信息」。但那只是
    // localStorage 里 formData 丢了（换设备/清缓存/从服务端同步回来的占位任务），
    // 后端完全有能力兜底：BYOK 用户用自己的凭据，站点模式用站点默认配置。
    // 让用户对着一条能看的笔记却问不了问题，是没有必要的限制。

    addMessage(taskId, { role: 'user', content: question })
    setInput('')
    setLoading(true)
    try {
      const history = messages.map(message => ({ role: message.role, content: message.content }))
      const res = await askQuestion({
        task_id: taskId,
        question,
        history,
        // 可能为空：交给后端按站点默认 / 用户 BYOK 凭据解析
        provider_id: modelConfig.providerId || undefined,
        model_name: modelConfig.modelName || undefined,
      })
      addMessage(taskId, { role: 'assistant', content: res.answer, sources: res.sources })
    } catch (error) {
      // 全局拦截器已经弹过后端返回的真实原因（askQuestion 现在带 suppressToast，
      // 所以这里不会再双重弹）。这里只补一句上下文，并不吞掉错误对象。
      const detail = (error as { msg?: string })?.msg
      toast.error(detail ? `问答失败：${detail}` : '问答请求失败，请稍后重试')
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

      <div className={body} ref={bodyRef} onScroll={handleBodyScroll}>
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
              // 中文/日文输入法用 Enter 选词时也会触发 keydown，但那是「确认候选词」
              // 不是「发送」。isComposing（或 keyCode 229 兼容旧浏览器）能区分二者 ——
              // 缺这个判断时，用户每选一次词就会发一次半截问题。
              if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
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
