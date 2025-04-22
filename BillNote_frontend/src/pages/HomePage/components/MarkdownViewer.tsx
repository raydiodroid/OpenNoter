import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, Download, FileText, ArrowRight } from 'lucide-react'
import { toast } from 'sonner' // 你可以换成自己的通知组件

import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { solarizedlight as codeStyle } from 'react-syntax-highlighter/dist/cjs/styles/prism'
import 'github-markdown-css/github-markdown-light.css'
import { FC } from 'react'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import { useTaskStore } from '@/store/taskStore'
interface MarkdownViewerProps {
  content: string
  status: 'idle' | 'loading' | 'success'
}

const MarkdownViewer: FC<MarkdownViewerProps> = ({ content, status }) => {
  const [copied, setCopied] = useState(false)
  const getCurrentTask = useTaskStore.getState().getCurrentTask
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
              components={{
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
