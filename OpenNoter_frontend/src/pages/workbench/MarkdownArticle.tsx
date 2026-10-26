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
import { API_BASE, apiUrl, staticUrl } from '@/lib/api'
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
        // 后端截图是 `/static/...`，其余相对路径走 `/api`。
        //
        // 为什么要 staticUrl 而不是原样用 src：桌面端页面源是 `tauri://localhost`，
        // `<img src="/static/x.jpg">` 会被解析成 `tauri://localhost/static/x.jpg`
        // ——frontendDist 里没有这个路径，必然 404。staticUrl 只做「前缀本地化」，
        // Web 同源部署下 `API_ORIGIN` 为空串，输出与改动前逐字节一致。
        //
        // 为什么不能在这里拼任何凭据：`<img>` 是浏览器发起的裸 GET，
        // Authorization / x-api-key / 跨站 Cookie 一个都带不上。所以 F9 的凭据
        // 只能由后端写进 URL 的 query（per-task 长期令牌 `?t=<token>`，
        // 见 backend/app/utils/static_signing.py）。本处**只换前缀、不动 query**：
        // 用 new URL() 重建 query 会改变参数编码/顺序，可能让令牌失效 → 403。
        // 另外**不要**改成 TTL 签名：截图 URL 在生成笔记时就被烧进 markdown 并持久化，
        // 带过期时间的签名会让历史笔记的截图永久破图（见 _verify/FIX-P0-F9-CORRECTION.md）。
        const resolved =
          typeof src === 'string'
            ? src.startsWith('/static/')
              ? staticUrl(src)
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
