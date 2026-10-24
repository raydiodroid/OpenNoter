import { useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeSlug from 'rehype-slug'
import Zoom from 'react-medium-image-zoom'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { ActionButton, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import PlayIcon from '@react-spectrum/s2/icons/Play'
import CopyIcon from '@react-spectrum/s2/icons/Copy'
import OpenInIcon from '@react-spectrum/s2/icons/OpenIn'

import 'react-medium-image-zoom/dist/styles.css'
import 'katex/dist/katex.min.css'
import { API_BASE, apiUrl } from '@/lib/api'
import { toast } from '@/lib/toast'
import { useThemeStore } from '@/store/themeStore'

const codeBlock = style({
  position: 'relative',
  marginY: 16,
  borderRadius: 'lg',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  overflow: 'hidden',
})

const codeHeader = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  paddingX: 12,
  paddingY: 4,
  backgroundColor: 'layer-2',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 1,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

/** 语言标签：等宽 12px + Sentence case，不做大写/宽字距 */
const lang = style({
  font: 'code-sm',
  color: 'neutral-subdued',
})

const imageWrap = style({
  display: 'flex',
  justifyContent: 'center',
  marginY: 24,
})

const image = style({
  maxWidth: 'full',
  maxHeight: 520,
  borderRadius: 'lg',
  cursor: 'zoom-in',
  objectFit: 'cover',
})

/** 目录锚点跳转：LLM 生成的锚点常与标题文本不完全一致，精确失败后按文本模糊匹配 */
function scrollToAnchor(href: string) {
  const id = decodeURIComponent(href.slice(1))
  let target = document.getElementById(id)

  if (!target) {
    const normalize = (value: string) => value.replace(/[-：:\s*[\]]/g, '').toLowerCase()
    const search = normalize(id)
    const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6')
    for (const heading of Array.from(headings)) {
      const text = normalize(heading.textContent || '')
      if (!text) continue
      if (text.includes(search) || search.includes(text)) {
        target = heading as HTMLElement
        break
      }
    }
  }

  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } else {
    toast.error('未找到对应章节')
  }
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const isDark = useThemeStore(s => s.theme === 'dark')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      toast.success('代码已复制')
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('复制失败')
    }
  }

  return (
    <div className={codeBlock}>
      <div className={codeHeader}>
        <span className={lang}>{language || 'text'}</span>
        <ActionButton isQuiet size="XS" onPress={copy}>
          <CopyIcon />
          <Text>{copied ? '已复制' : '复制'}</Text>
        </ActionButton>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={isDark ? oneDark : oneLight}
        customStyle={{
          margin: 0,
          padding: '16px',
          background: 'transparent',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          lineHeight: 1.6,
        }}
        codeTagProps={{ style: { fontFamily: 'var(--font-mono)' } }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

export default function MarkdownArticle({ content }: { content: string }) {
  const components = useMemo(
    () => ({
      a({ href, children, ...props }: any) {
        const firstChild = Array.isArray(children) ? children[0] : children
        const firstText = typeof firstChild === 'string' ? firstChild : ''
        const isOrigin = firstText.startsWith('原片 @')
        const timeMatch = firstText.match(/原片 @ (\d{2}:\d{2})/)

        if (isOrigin) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="note-origin-link"
              {...props}
            >
              <PlayIcon />
              <span>原片（{timeMatch?.[1] ?? '跳转'}）</span>
            </a>
          )
        }

        if (href?.startsWith('#')) {
          return (
            <a
              href={href}
              onClick={event => {
                event.preventDefault()
                scrollToAnchor(href)
              }}
              {...props}
            >
              {children}
            </a>
          )
        }

        return (
          <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
            {children}
            {href?.startsWith('http') ? (
              <OpenInIcon
                UNSAFE_style={{
                  display: 'inline-block',
                  verticalAlign: '-2px',
                  marginInlineStart: 2,
                }}
              />
            ) : null}
          </a>
        )
      },

      img({ src, alt, ...props }: any) {
        // 后端截图是 /static/...（nginx 已直连），其余相对路径走 /api
        const resolved =
          typeof src === 'string'
            ? src.startsWith('/static/')
              ? src
              : src.startsWith('/')
                ? apiUrl(src)
                : src
            : src
        return (
          <span className={imageWrap}>
            <Zoom wrapElement="span">
              <img
                {...props}
                src={resolved}
                alt={alt || ''}
                referrerPolicy="no-referrer"
                className={image}
              />
            </Zoom>
          </span>
        )
      },

      code({ className, children, ...props }: any) {
        const match = /language-(\w+)/.exec(className || '')
        const text = String(children).replace(/\n$/, '')

        if (match) {
          return <CodeBlock language={match[1]} code={text} />
        }

        return (
          <code className={className} {...props}>
            {children}
          </code>
        )
      },

      pre({ children }: { children?: ReactNode }) {
        return <>{children}</>
      },
    }),
    []
  )

  // 去掉后端附加的「来源链接」引用行
  const body = content.replace(/^>\s*来源链接：[^\n]*\n*/m, '')

  return (
    <div className="note-body" data-api-base={API_BASE}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeSlug]}
        components={components}
      >
        {body}
      </ReactMarkdown>
    </div>
  )
}
