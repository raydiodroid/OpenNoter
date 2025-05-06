import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, Download, ArrowRight,Play,ExternalLink  } from 'lucide-react'
import { toast } from 'react-hot-toast'
import Error from '@/components/Lottie/error.tsx'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark as codeStyle } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import gfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import 'github-markdown-css/github-markdown-light.css'
import { FC } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { useTaskStore } from '@/store/taskStore'
import { noteStyles } from '@/constant/note.ts'
import { MarkdownHeader } from '@/pages/HomePage/components/MarkdownHeader.tsx'
import TranscriptViewer from "@/pages/HomePage/components/transcriptViewer.tsx";

interface VersionNote {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at?: string
}

interface MarkdownViewerProps {
  content: string | VersionNote[]
  status: 'idle' | 'loading' | 'success' | 'failed'
}

const steps = [
  { label: '解析链接', key: 'PARSING' },
  { label: '下载音频', key: 'DOWNLOADING' },
  { label: '转写文字', key: 'TRANSCRIBING' },
  { label: '总结内容', key: 'SUMMARIZING' },
  { label: '保存完成', key: 'SUCCESS' },
]

const MarkdownViewer: FC<MarkdownViewerProps> = ({ status }) => {
  const [copied, setCopied] = useState(false)
  const [currentVerId, setCurrentVerId] = useState<string>('')
  const [selectedContent, setSelectedContent] = useState<string>('')
  const [modelName, setModelName] = useState<string>('')
  const [style, setStyle] = useState<string>('')
  const [createTime, setCreateTime] = useState<string>('')

  const getCurrentTask = useTaskStore.getState().getCurrentTask
  const currentTask = useTaskStore(state => state.getCurrentTask())
  const taskStatus = currentTask?.status || 'PENDING'
  const retryTask = useTaskStore.getState().retryTask
  const isMultiVersion = Array.isArray(currentTask?.markdown)
  const [showTranscribe, setShowTranscribe]=useState(false)
  // 多版本内容处理
  useEffect(() => {
    if (!currentTask) return;

    if (!isMultiVersion) {
      setCurrentVerId('') // 清空旧版本 ID
      setModelName(currentTask.formData.model_name)
      setStyle(currentTask.formData.style)
      setCreateTime(currentTask.createdAt)
      setSelectedContent(currentTask?.markdown)
    } else {
      const latestVerId = currentTask.markdown[currentTask.markdown.length - 1]?.ver_id
      setCurrentVerId(latestVerId) // 重置为最新版本
    }
  }, [currentTask?.id,taskStatus])
  useEffect(() => {
    if (!currentTask || !isMultiVersion) return;

    const currentVer = currentTask.markdown.find(v => v.ver_id === currentVerId)
    if (currentVer) {
      setModelName(currentVer.model_name)
      setStyle(currentVer.style)
      setCreateTime(currentVer.created_at || '')
      setSelectedContent(currentVer.content)
    }
  }, [currentVerId, currentTask?.id])
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(selectedContent)
      setCopied(true)
      toast.success('已复制到剪贴板')
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      toast.error('复制失败')
    }
  }

  const handleDownload = () => {
    const task = getCurrentTask()
    const name = task?.audioMeta.title || 'note'
    const blob = new Blob([selectedContent], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${name}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  if (status === 'loading') {
    return (
        <div className="flex h-screen w-full flex-col items-center justify-center space-y-4 text-neutral-500">
          <StepBar steps={steps} currentStep={taskStatus} />
          <Loading className="h-5 w-5" />
          <div className="text-center text-sm">
            <p className="text-lg font-bold">正在生成笔记，请稍候…</p>
            <p className="mt-2 text-xs text-neutral-500">这可能需要几秒钟时间，取决于视频长度</p>
          </div>
        </div>
    )
  }

  if (status === 'idle') {
    return (
        <div className="flex h-screen w-full flex-col items-center justify-center space-y-3 text-neutral-500">
          <Idle />
          <div className="text-center">
            <p className="text-lg font-bold">输入视频链接并点击“生成笔记”</p>
            <p className="mt-2 text-xs text-neutral-500">支持哔哩哔哩、YouTube 、抖音等视频平台</p>
          </div>
        </div>
    )
  }

  if (status === 'failed' && !isMultiVersion) {
    return (
        <div className="flex h-screen w-full flex-col items-center justify-center gap-4 space-y-3">
          <Error />
          <div className="text-center">
            <p className="text-lg font-bold text-red-500">笔记生成失败</p>
            <p className="mt-2 mb-2 text-xs text-red-400">请检查后台或稍后再试</p>
            <Button onClick={() => retryTask(currentTask.id)} size="lg">重试</Button>
          </div>
        </div>
    )
  }


  return (
      <div className="flex h-screen w-full flex-col overflow-hidden">
        <MarkdownHeader
            currentTask={currentTask}
            isMultiVersion={isMultiVersion}
            currentVerId={currentVerId}
            setCurrentVerId={setCurrentVerId}
            modelName={modelName}
            style={style}
            noteStyles={noteStyles}
            onCopy={handleCopy}
            onDownload={handleDownload}
            createAt={createTime}
            showTranscribe={showTranscribe}
            setShowTranscribe={setShowTranscribe}
        />

        {/* 中间内容区域：滚动容器 */}
        <div className="flex-1 flex overflow-hidden bg-white  py-2">
          {selectedContent && selectedContent !== 'loading' && selectedContent !== 'empty' ? (
              <>
              <ScrollArea className="w-full ">
              <div className={"w-full px-2 markdown-body"}>


                    <ReactMarkdown
                        remarkPlugins={[gfm, remarkMath]}
                        rehypePlugins={[rehypeKatex]}
                        components={{
                          // Headings with improved styling and anchor links
                          h1: ({ children, ...props }) => (
                              <h1
                                  className="scroll-m-20 text-3xl font-extrabold tracking-tight text-primary lg:text-4xl my-6"
                                  {...props}
                              >
                                {children}
                              </h1>
                          ),
                          h2: ({ children, ...props }) => (
                              <h2
                                  className="scroll-m-20 border-b pb-2 text-2xl font-semibold tracking-tight text-primary first:mt-0 mt-10 mb-4"
                                  {...props}
                              >
                                {children}
                              </h2>
                          ),
                          h3: ({ children, ...props }) => (
                              <h3
                                  className="scroll-m-20 text-xl font-semibold tracking-tight text-primary mt-8 mb-4"
                                  {...props}
                              >
                                {children}
                              </h3>
                          ),
                          h4: ({ children, ...props }) => (
                              <h4
                                  className="scroll-m-20 text-lg font-semibold tracking-tight text-primary mt-6 mb-2"
                                  {...props}
                              >
                                {children}
                              </h4>
                          ),

                          // Paragraphs with better line height
                          p: ({ children, ...props }) => (
                              <p className="leading-7 [&:not(:first-child)]:mt-6" {...props}>
                                {children}
                              </p>
                          ),

                          // Enhanced links with special handling for "原片" links
                          a: ({ href, children, ...props }) => {
                            const isOriginLink = typeof children[0] === 'string' && (children[0] as string).startsWith('原片 @')

                            if (isOriginLink) {
                              const timeMatch = (children[0] as string).match(/原片 @ (\d{2}:\d{2})/)
                              const timeText = timeMatch ? timeMatch[1] : '原片'

                              return (
                                  <span className="origin-link inline-flex my-2">
                  <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
                      {...props}
                  >
                    <Play className="h-3.5 w-3.5" />
                    <span>原片（{timeText}）</span>
                  </a>
                </span>
                              )
                            }

                            // Default link styling with external indicator
                            return (
                                <a
                                    href={href}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="font-medium text-primary underline underline-offset-4 hover:text-primary/80 inline-flex items-center gap-0.5"
                                    {...props}
                                >
                                  {children}
                                  {href?.startsWith('http') && <ExternalLink className="h-3 w-3 inline-block ml-0.5" />}
                                </a>
                            )
                          },

                          // Enhanced image with zoom capability
                          img: ({ node, ...props }) => (
                              <div className="my-8 flex justify-center">
                                <Zoom>
                                  <img
                                      {...props}
                                      className="rounded-lg shadow-md max-w-full cursor-zoom-in object-cover transition-all hover:shadow-lg"
                                      style={{ maxHeight: '500px' }}
                                  />
                                </Zoom>
                              </div>
                          ),

                          // Better strong/bold text
                          strong: ({ children, ...props }) => (
                              <strong className="font-bold text-primary" {...props}>
                                {children}
                              </strong>
                          ),

                          // Enhanced list items with support for "fake headings"
                          li: ({ children, ...props }) => {
                            const rawText = String(children)
                            const isFakeHeading = /^(\*\*.+\*\*)$/.test(rawText.trim())

                            if (isFakeHeading) {
                              return (
                                  <div className="text-lg font-bold my-4 text-primary">
                                    {children}
                                  </div>
                              )
                            }

                            return (
                                <li className="my-1" {...props}>
                                  {children}
                                </li>
                            )
                          },

                          // Enhanced unordered lists
                          ul: ({ children, ...props }) => (
                              <ul className="my-6 ml-6 list-disc [&>li]:mt-2" {...props}>
                                {children}
                              </ul>
                          ),

                          // Enhanced ordered lists
                          ol: ({ children, ...props }) => (
                              <ol className="my-6 ml-6 list-decimal [&>li]:mt-2" {...props}>
                                {children}
                              </ol>
                          ),

                          // Enhanced blockquotes
                          blockquote: ({ children, ...props }) => (
                              <blockquote
                                  className="mt-6 border-l-4 border-primary/20 pl-4 italic text-muted-foreground"
                                  {...props}
                              >
                                {children}
                              </blockquote>
                          ),

                          // Enhanced code blocks with syntax highlighting and copy button
                          code: ({ inline, className, children, ...props }) => {
                            const match = /language-(\w+)/.exec(className || '')
                            const codeContent = String(children).replace(/\n$/, '')

                            if (!inline && match) {
                              return (
                                  <div className="group relative my-6 overflow-hidden rounded-lg border bg-muted shadow-sm">
                                    <div className="flex items-center justify-between bg-muted px-4 py-1.5 text-sm font-medium text-muted-foreground">
                                      <div>{match[1].toUpperCase()}</div>
                                      <button
                                          onClick={() => {
                                            navigator.clipboard.writeText(codeContent)
                                            toast.success('代码已复制')
                                          }}
                                          className="flex items-center gap-1 rounded-md bg-background/80 px-2 py-1 text-xs font-medium hover:bg-background transition-colors"
                                      >
                                        <Copy className="h-3.5 w-3.5" />
                                        复制
                                      </button>
                                    </div>
                                    <SyntaxHighlighter
                                        style={codeStyle}
                                        language={match[1]}
                                        PreTag="div"
                                        className="!m-0 !bg-muted !p-0"
                                        customStyle={{
                                          margin: 0,
                                          padding: '1rem',
                                          background: 'transparent',
                                          fontSize: '0.9rem',
                                        }}
                                        {...props}
                                    >
                                      {codeContent}
                                    </SyntaxHighlighter>
                                  </div>
                              )
                            }

                            // Inline code styling
                            return (
                                <code
                                    className="relative rounded bg-muted px-[0.3rem] py-[0.2rem] font-mono text-sm"
                                    {...props}
                                >
                                  {children}
                                </code>
                            )
                          },

                          // Enhanced tables
                          table: ({ children, ...props }) => (
                              <div className="my-6 w-full overflow-y-auto">
                                <table className="w-full border-collapse text-sm" {...props}>
                                  {children}
                                </table>
                              </div>
                          ),

                          // Table headers
                          th: ({ children, ...props }) => (
                              <th
                                  className="border border-muted-foreground/20 px-4 py-2 text-left font-medium [&[align=center]]:text-center [&[align=right]]:text-right"
                                  {...props}
                              >
                                {children}
                              </th>
                          ),

                          // Table cells
                          td: ({ children, ...props }) => (
                              <td
                                  className="border border-muted-foreground/20 px-4 py-2 text-left [&[align=center]]:text-center [&[align=right]]:text-right"
                                  {...props}
                              >
                                {children}
                              </td>
                          ),

                          // Horizontal rule
                          hr: ({ ...props }) => (
                              <hr className="my-8 border-muted-foreground/20" {...props} />
                          ),
                        }}
                    >
                      {selectedContent}
                    </ReactMarkdown>

                    </div>
              </ScrollArea>
                {
                    showTranscribe && (
                        <div className={'ml-2 w-2/4'}>
                          <TranscriptViewer/>
                        </div>

                    )

                }

              </>
          ) : (
              <div className="flex h-full w-full items-center justify-center">
                <div className="w-[300px] flex-col justify-items-center">
                  <div className="bg-primary-light mb-4 flex h-16 w-16 items-center justify-center rounded-full">
                    <ArrowRight className="text-primary h-8 w-8" />
                  </div>
                  <p className="mb-2 text-neutral-600">输入视频链接并点击"生成笔记"按钮</p>
                  <p className="text-xs text-neutral-500">支持哔哩哔哩、YouTube等视频网站</p>
                </div>
              </div>
          )}
        </div>
      </div>
  )
}

export default MarkdownViewer
