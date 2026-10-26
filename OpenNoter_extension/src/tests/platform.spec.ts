import { describe, expect, it } from 'vitest'
import { detectPlatform } from '../logic/platform'

/**
 * 平台识别矩阵（F12）。
 *
 * ## 为什么要有这份测试
 *
 * `detectPlatform` 曾经是「子串包含」启发式：
 *
 *     if (url.includes('douyin')) return 'douyin'
 *
 * 这也是后端**已经修掉**的那类问题。修完如果只靠人工看，很容易再退回去，
 * 所以这里把行为钉成矩阵，分三组：
 *
 *   1. **必须识别**（含旧实现认不出的短链 / Shorts / bangumi）；
 *   2. **必须不误判**（子串欺骗类，这是回归的关键防线）；
 *   3. **边界**（空值、无协议、脏输入不抛异常）。
 *
 * 另一层价值：它与 `backend/app/utils/url_parser.py::PLATFORM_HOSTS` 是镜像
 * 关系。哪天后端加了新域名而后端测试没覆盖这里，本文件的矩阵就是提醒。
 */
describe('detectPlatform 识别', () => {
  it.each([
    // —— 旧实现本来就能识别的，不能回归 ——
    ['https://www.bilibili.com/video/BV1xx411c7mD', 'bilibili'],
    ['https://www.bilibili.com/video/BV1xx411c7mD?p=2', 'bilibili'],
    ['https://m.bilibili.com/video/BV1xx411c7mD', 'bilibili'],
    ['https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube'],
    ['https://youtu.be/dQw4w9WgXcQ', 'youtube'],
    ['https://www.douyin.com/video/7123456789', 'douyin'],
    ['https://www.kuaishou.com/short-video/abc', 'kuaishou'],

    // —— F12 新增：短链域名 ——
    ['https://b23.tv/abcdefg', 'bilibili'],
    ['https://bili2233.cn/xyz', 'bilibili'],
    ['https://v.douyin.com/iABCdef/', 'douyin'],
    ['https://jx.douyin.com/abc', 'douyin'],

    // —— F12 新增：路径形态（旧实现认不出）——
    ['https://www.youtube.com/shorts/dQw4w9WgXcQ', 'youtube'],
    ['https://www.youtube.com/live/dQw4w9WgXcQ', 'youtube'],
    ['https://www.bilibili.com/bangumi/play/ep123456', 'bilibili'],
    ['https://www.bilibili.com/list/watchlater?bvid=BV1xx411c7mD', 'bilibili'],
  ])('识别 %s → %s', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })
})

describe('detectPlatform 不误判（子串欺骗回归）', () => {
  it.each([
    // 旧实现 `url.includes('douyin')` 会把这些判成 douyin
    ['https://evil.com/?x=douyin', null],
    ['https://evil.com/douyin/video/1', null],
    ['https://example.com/path?ref=kuaishou', null],
    ['https://attacker.net/#bilibili', null],

    // 后缀相似的假域名（必须按「完全相等或 .domain 后缀」判断）
    ['https://evil-bilibili.com/video/BV1xx', null],
    ['https://notbilibili.com/video/BV1xx', null],
    ['https://bilibili.com.evil.com/video/BV1xx', null],
    ['https://youtube.com.evil.com/watch?v=x', null],

    // 其它真实站点
    ['https://vimeo.com/12345', null],
    ['https://twitter.com/i/status/123', null],
  ])('%s 不应识别为任何平台', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })
})

describe('detectPlatform 边界', () => {
  it('空值返回 null 而不是抛异常', () => {
    expect(detectPlatform('')).toBeNull()
    expect(detectPlatform(null)).toBeNull()
    expect(detectPlatform(undefined)).toBeNull()
  })

  it('没有协议的域名也能识别（用户常直接粘贴）', () => {
    expect(detectPlatform('www.bilibili.com/video/BV1xx411c7mD')).toBe('bilibili')
  })

  it('畸形输入不抛异常', () => {
    for (const bad of ['http://', '://x', '   ', 'not a url at all', 'https://']) {
      expect(() => detectPlatform(bad)).not.toThrow()
      expect(detectPlatform(bad)).toBeNull()
    }
  })
})
