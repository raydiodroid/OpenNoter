import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, Download, FileText, ArrowRight } from 'lucide-react'
import { toast } from 'react-hot-toast' // 你可以换成自己的通知组件
import Error from '@/components/Lottie/error.tsx'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark as codeStyle } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'

import 'github-markdown-css/github-markdown-light.css'
import { FC } from 'react'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import { useTaskStore } from '@/store/taskStore'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import gfm from 'remark-gfm'
import remarkMath from 'remark-math'

import 'katex/dist/katex.min.css'

import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

interface MarkdownViewerProps {
  content: string
  status: 'idle' | 'loading' | 'success' | 'failed'
}

const steps = [
  { label: '解析链接', key: 'PARSING' },
  { label: '下载音频', key: 'DOWNLOADING' },
  { label: '转写文字', key: 'TRANSCRIBING' },
  { label: '总结内容', key: 'SUMMARIZING' },
  { label: '保存完成', key: 'SUCCESS' },
]

const MarkdownViewer: FC<MarkdownViewerProps> = ({ content, status }) => {
  const [copied, setCopied] = useState(false)
  const getCurrentTask = useTaskStore.getState().getCurrentTask
  const currentTask = useTaskStore(state => state.getCurrentTask())
  const taskStatus = currentTask?.status || 'PENDING'
  const retryTask = useTaskStore.getState().retryTask
  let firstHeadingRendered = false

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      toast.success('已复制到剪贴板')
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      toast.error(`复制失败${e}`)
      toast.error('复制失败', e)
    }
  }

  const handleDownload = () => {
    const currentTask = getCurrentTask()
    const currentTaskName = currentTask?.audioMeta.title

    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${currentTaskName}.md`
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
  } else if (status === 'idle') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center space-y-3 text-neutral-500">
        <Idle></Idle>

        <div className="text-center">
          <p className="text-lg font-bold">输入视频链接并点击“生成笔记”</p>
          <p className="mt-2 text-xs text-neutral-500">支持哔哩哔哩、YouTube 等视频平台</p>
        </div>
      </div>
    )
  } else if (status === 'failed') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center gap-4 space-y-3">
        <Error /> {/* 你可以换成 Failed 动画 */}
        <div className="text-center">
          <p className="text-lg font-bold text-red-500">笔记生成失败</p>
          <p className="mt-2 mb-2 text-xs text-red-400">请检查后台或稍后再试</p>
          <Button
            onClick={() => {
              retryTask(currentTask.id)
            }}
            size="lg"
          >
            重试
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部操作栏 */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-xl font-semibold text-neutral-900">
          <FileText className="text-primary h-5 w-5" />
          笔记内容
        </h2>
        <div className="flex items-center gap-2">
          <Button onClick={handleCopy} variant="outline" size="sm">
            <Copy className="mr-1 h-4 w-4" />
            {copied ? '已复制' : '复制'}
          </Button>
          <Button onClick={handleDownload} variant="outline" size="sm">
            <Download className="mr-1 h-4 w-4" />
            导出 Markdown
          </Button>
        </div>
      </div>

      {/* 滚动容器 */}

      <div className="overflow-y-auto">
        {(content && content != 'loading') || content != 'empty' ? (
          <div className="markdown-body flex-1 bg-white">
            {' '}
            <ReactMarkdown
                remarkPlugins={[gfm,remarkMath]}
                rehypePlugins={[rehypeKatex]}
              components={{
                img: ({ node, ...props }) => (
                    <Zoom>
                    <img
                        {...props}
                        className="rounded-lg shadow-md max-w-full cursor-pointer mx-auto my-4  max-h-[300px]  "
                        alt={props.alt || ''}
                    />
                    </Zoom>
                ),
                strong({ node, children, ...props }){
                  return <strong className="text-lg font-bold my-4 text-blue-600"  {...props}>{children}</strong>
                },
                li({ node, children, ...props }) {
                  const rawText = String(children)

                  // 检测是否是“加粗的编号开头项”，比如 "**2. 算法摄影的兴起**"
                  const isFakeHeading = /^(\*\*.+\*\*)$/.test(rawText.trim())

                  if (isFakeHeading) {
                    return (
                        <p className="text-lg  font-bold my-4 text-gray-800 text-left">
                          {children}
                        </p>
                    )
                  }

                  return <li {...props}>{children}</li>
                },
                h1({ node, children, ...props }) {
                  return (
                      <h1
                          className="text-3xl text-center font-bold my-6 text-blue-600"
                          {...props}
                      >
                        {children}
                      </h1>
                  )
                },
                h2({ node, children, ...props }) {
                  return (
                      <h2
                          className="text-2xl font-bold my-4 text-blue-600"
                          {...props}
                      >
                        {children}
                      </h2>
                  )
                },

                code({ node, inline, className, children, ...props }) {
                  const match = /language-(\w+)/.exec(className || '')
                  const codeContent = String(children).replace(/\n$/, '')

                  if (!inline && match) {
                    return (
                      <div className="group relative">
                        <SyntaxHighlighter
                            style={codeStyle}
                            language={match[1]}
                            PreTag="div"
                            {...props}
                        >
                          {codeContent}
                        </SyntaxHighlighter>

                        <button
                          onClick={() => {
                            console.log('点击负责')
                            navigator.clipboard.writeText(codeContent)
                            toast.success('代码已复制')
                          }}
                          className="absolute top-2 right-2 hidden items-center gap-1 rounded border border-gray-300 bg-white/70 px-2 py-1 text-xs shadow-sm transition group-hover:flex hover:bg-white"
                        >
                          <Copy className="h-3 w-3" />
                          复制
                        </button>
                      </div>
                    )
                  }

                  return (
                    <code className="rounded bg-gray-100 px-1 py-0.5 text-sm" {...props}>
                      {children}
                    </code>
                  )
                },
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="flex h-screen w-full items-center justify-center">
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
      {/*<div className="markdown-body flex-1 overflow-y-auto bg-white">*/}
      {/*    {content ? (*/}
      {/*      */}
      {/*    ) : (*/}
      {/*        <>*/}
      {/*            <div className="w-16 h-16 bg-primary-light rounded-full flex items-center justify-center mb-4">*/}
      {/*                <ArrowRight className="h-8 w-8 text-primary" />*/}
      {/*            </div>*/}
      {/*            <p className="text-neutral-600 mb-2">输入视频链接并点击"生成笔记"按钮</p>*/}
      {/*            <p className="text-xs text-neutral-500">支持哔哩哔哩、YouTube、腾讯视频和爱奇艺</p>*/}
      {/*        </>*/}
      {/*    )}*/}
      {/*</div>*/}
    </div>
  )
}

export default MarkdownViewer
