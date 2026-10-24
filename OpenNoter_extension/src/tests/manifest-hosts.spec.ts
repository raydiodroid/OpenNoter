import { describe, expect, it } from 'vitest'
import { getManifest } from '../manifest'

describe('manifest host permissions', () => {
  it('does not request all_urls or wildcard all hosts', async () => {
    const manifest = await getManifest()
    expect(manifest.host_permissions).toBeDefined()
    expect(manifest.host_permissions).not.toContain('*://*/*')
    expect(manifest.host_permissions).not.toContain('<all_urls>')

    const matches = manifest.content_scripts?.[0]?.matches ?? []
    expect(matches).not.toContain('<all_urls>')
    expect(matches).not.toContain('*://*/*')

    // 视频站仍可用
    expect(manifest.host_permissions!.some(p => p.includes('bilibili.com'))).toBe(true)
    expect(manifest.host_permissions!.some(p => p.includes('youtube.com'))).toBe(true)
    expect(manifest.host_permissions!.some(p => p.includes('douyin.com'))).toBe(true)
    expect(manifest.host_permissions!.some(p => p.includes('kuaishou.com'))).toBe(true)

    // 本机 / 已知 OpenNoter 通信
    expect(manifest.host_permissions!).toEqual(
      expect.arrayContaining([
        'http://localhost/*',
        'http://127.0.0.1/*',
        'https://opennoter.raydiodroid.top/*',
      ]),
    )

    // content script 覆盖视频站与 OpenNoter 设置页（Cookie 桥）
    expect(matches.some(m => m.includes('bilibili.com'))).toBe(true)
    expect(matches.some(m => m.includes('localhost'))).toBe(true)
  })
})
