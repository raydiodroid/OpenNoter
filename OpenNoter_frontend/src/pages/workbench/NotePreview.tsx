import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Content,
  Heading,
  IllustratedMessage,
  ProgressCircle,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import AIGenerate from '@react-spectrum/s2/illustrations/gradient/generic1/AIGenerate'
import ErrorIllustration from '@react-spectrum/s2/illustrations/linear/Error'

import NoteToolbar from '@/pages/workbench/NoteToolbar'
import MarkdownArticle from '@/pages/workbench/MarkdownArticle'
import MindmapView from '@/pages/workbench/MindmapView'
import TranscriptPane from '@/pages/workbench/TranscriptPane'
import ChatPane from '@/pages/workbench/ChatPane'
import VideoBanner from '@/pages/workbench/VideoBanner'
import GenerateProgress from '@/pages/workbench/GenerateProgress'
import { selectCurrentTask, useTaskStore } from '@/store/taskStore'
import { generateNote } from '@/services/note'
import { toast } from '@/lib/toast'

const preview = style({
  display: 'flex',
  flexDirection: 'column',
  height: 'full',
  minHeight: 0,
  overflow: 'hidden',
  backgroundColor: 'base',
})

const scroll = style({
  flexGrow: 1,
  minWidth: 0,
  minHeight: 0,
  overflowY: 'auto',
  paddingX: 32,
  paddingY: 32,
})

/** 正文容器：统一白底，只用阅读宽度和留白控制节奏 */
const paper = style({
  maxWidth: 780,
  marginX: 'auto',
})

const split = style({
  display: 'flex',
  flexGrow: 1,
  minHeight: 0,
  minWidth: 0,
})

const side = style({
  width: 360,
  flexShrink: 0,
  minHeight: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 0,
  borderEndWidth: 0,
  borderStartWidth: 1,
})

const sideChat = style({
  width: 400,
  flexShrink: 0,
  minHeight: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 0,
  borderEndWidth: 0,
  borderStartWidth: 1,
})

const chatFull = style({
  flexGrow: 1,
  minHeight: 0,
})

const centered = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: 'full',
  padding: 32,
})

const emptyCard = style({
  width: 'full',
  maxWidth: 560,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 24,
  textAlign: 'center',
})

/** 失败原因代码块：等宽、可换行、左对齐（长错误串要能看全） */
const failureBox = style({
  width: 'full',
  maxWidth: 520,
  paddingX: 16,
  paddingY: 12,
  borderRadius: 'default',
  backgroundColor: 'gray-100',
  textAlign: 'start',
})

const failureText = style({
  font: 'code',
  color: 'neutral',
  margin: 0,
  // 后端错误串可能很长（含 URL / 堆栈摘要），必须允许换行
  wordBreak: 'break-word',
  whiteSpace: 'pre-wrap',
})

const steps = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  flexWrap: 'wrap',
  font: 'detail',
  color: 'neutral-subdued',
})


export default function NotePreview({ onRequestMoreSpace }: { onRequestMoreSpace?: () => void }) {
  const task = useTaskStore(selectCurrentTask)
  const updateTask = useTaskStore(s => s.updateTask)

  const [verId, setVerId] = useState('')
  const [view, setView] = useState<'doc' | 'map'>('doc')
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [chatMode, setChatMode] = useState<false | 'half' | 'full'>(false)
  const [retrying, setRetrying] = useState(false)

  const versions = useMemo(() => task?.markdown ?? [], [task?.markdown])

  useEffect(() => {
    setVerId(versions[0]?.ver_id ?? '')
    setView('doc')
    setTranscriptOpen(false)
    setChatMode(false)
    // 只在切换任务时重置视图状态
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.id])

  useEffect(() => {
    if (versions.length && !versions.some(version => version.ver_id === verId)) {
      setVerId(versions[0].ver_id)
    }
  }, [verId, versions])

  const current = useMemo(
    () => versions.find(version => version.ver_id === verId) ?? versions[0],
    [verId, versions],
  )

  if (!task) {
    return (
      <div className={preview}>
        <div className={centered}>
          <div className={emptyCard}>
            <IllustratedMessage size="L">
              <AIGenerate />
              <Heading>把视频变成一篇结构化笔记</Heading>
              <Content>
                支持哔哩哔哩、YouTube、抖音、快手与本地视频。粘贴链接、选好模型和风格，点「生成笔记」。
              </Content>
            </IllustratedMessage>
            <p className={steps}>粘贴链接 · 选择模型与风格 · 生成笔记</p>
          </div>
        </div>
      </div>
    )
  }

  if (task.status === 'FAILED') {
    const retry = async () => {
      setRetrying(true)
      try {
        await generateNote({ ...task.formData, task_id: task.id })
        updateTask(task.id, { status: 'PENDING' })
        toast.info('已重新提交')
      } catch (error: any) {
        if (error?.data?.reason === 'transcriber_model_not_ready') {
          toast.error(error.data.downloading ? '转写模型正在下载中' : '请先下载转写模型')
        } else {
          toast.error(error?.msg || '重新提交失败，请稍后再试')
        }
      } finally {
        setRetrying(false)
      }
    }

    // 失败原因：后端在状态文件/DB 里已经有明确的 message（例如
    // 「B站下载失败: HTTP Error 412: Precondition Failed」），
    // 直接展示给用户才有用。
    //
    // 旧文案是「检查「部署监控」里的后端日志」—— 但监控页**没有日志面板**，
    // 用户照着找只会更困惑。这类"指向不存在的地方"的提示比没有提示更糟。
    const failureDetail = (task.message || '').trim()

    return (
      <div className={preview}>
        <div className={centered}>
          <div className={emptyCard}>
            <IllustratedMessage size="L">
              <ErrorIllustration />
              <Heading>笔记生成失败</Heading>
              {failureDetail ? (
                <>
                  <Content>下面是后端返回的具体原因：</Content>
                  <div className={failureBox}>
                    <p className={failureText}>{failureDetail}</p>
                  </div>
                  <Content>
                    常见原因：视频需要登录（在「设置 → 下载器」配置 Cookie）、
                    链接已失效、或平台临时限流。可以直接重试一次。
                  </Content>
                </>
              ) : (
                <Content>
                  后端没有返回具体原因。可以直接重试一次；若持续失败，
                  请到服务器查看容器日志（docker logs opennoter-backend）。
                </Content>
              )}
            </IllustratedMessage>
            <Button variant="accent" onPress={retry} isDisabled={retrying}>
              {retrying ? '提交中…' : '重试'}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (task.status !== 'SUCCESS') {
    return (
      <div className={preview}>
        <div className={centered}>
          <div className={emptyCard}>
            <GenerateProgress status={task.status} />
          </div>
        </div>
      </div>
    )
  }

  // SUCCESS 但正文还没到：刚从服务端同步过来的占位条目，正文由 useTaskPolling
  // 的 hydrateTask 补拉（F10）。这里必须显式给「加载中」，而不是渲染一篇空白笔记——
  // 空白页面会让用户以为笔记内容丢了。
  if (versions.length === 0) {
    return (
      <div className={preview}>
        <div className={centered}>
          <div className={emptyCard}>
            <ProgressCircle aria-label="正在加载笔记正文" isIndeterminate size="L" />
            <Heading level={2}>正在加载笔记正文…</Heading>
            <Content>正在从服务端取回这篇笔记的正文，稍等片刻。</Content>
          </div>
        </div>
      </div>
    )
  }

  const content = current?.content ?? ''
  const noteStyle = current?.style || task.formData.style
  const modelName = current?.model_name || task.formData.model_name
  const createdAt = current?.created_at || task.createdAt

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      toast.success('已复制到剪贴板')
    } catch {
      toast.error('复制失败')
    }
  }

  const exportMarkdown = () => {
    const name = task.audioMeta.title || 'note'
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${name}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <div className={preview}>
      <NoteToolbar
        versions={versions}
        currentVerId={current?.ver_id ?? ''}
        onVersionChange={setVerId}
        view={view}
        onViewChange={setView}
        transcriptOpen={transcriptOpen}
        onToggleTranscript={() => {
          const next = !transcriptOpen
          setTranscriptOpen(next)
          if (next) onRequestMoreSpace?.()
        }}
        chatOpen={chatMode !== false}
        onToggleChat={() => {
          const next = chatMode ? false : 'half'
          setChatMode(next)
          if (next) onRequestMoreSpace?.()
        }}
        onCopy={copy}
        onExport={exportMarkdown}
      />

      {view === 'map' ? (
        <div className={chatFull}>
          <MindmapView value={content} title={task.audioMeta.title} />
        </div>
      ) : chatMode === 'full' ? (
        <div className={chatFull}>
          <ChatPane
            taskId={task.id}
            mode="full"
            onModeChange={mode => setChatMode(mode)}
            onClose={() => setChatMode(false)}
          />
        </div>
      ) : (
        <div className={split}>
          <div className={scroll}>
            <div className={paper}>
              <VideoBanner
                audioMeta={task.audioMeta}
                videoUrl={task.formData.video_url}
                modelName={modelName}
                noteStyle={noteStyle}
                createdAt={createdAt}
              />
              {content ? (
                <MarkdownArticle content={content} />
              ) : (
                <div className={centered}>
                  <ProgressCircle aria-label="空内容" isIndeterminate />
                </div>
              )}
            </div>
          </div>

          {transcriptOpen ? (
            <div className={side}>
              <TranscriptPane segments={task.transcript?.segments ?? []} />
            </div>
          ) : null}

          {chatMode === 'half' ? (
            <div className={sideChat}>
              <ChatPane
                taskId={task.id}
                mode="half"
                onModeChange={mode => setChatMode(mode)}
                onClose={() => setChatMode(false)}
              />
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
