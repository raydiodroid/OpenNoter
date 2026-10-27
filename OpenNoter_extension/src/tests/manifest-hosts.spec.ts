import { describe, expect, it } from 'vitest'
import { getManifest } from '../manifest'

/** 视频站域名片段（这些是产品功能所需，不是部署信息）。 */
const VIDEO_HOSTS = ['bilibili.com', 'youtube.com', 'youtu.be', 'douyin.com', 'iesdouyin.com', 'kuaishou.com']

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

    // 本机 OpenNoter 通信（内置权限，开箱即用）
    expect(manifest.host_permissions!).toEqual(
      expect.arrayContaining([
        'http://localhost/*',
        'http://127.0.0.1/*',
      ]),
    )

    // **不得**硬编码任何具体公网域名：
    //   1. 公开仓库里等于公布部署者自己的域名；
    //   2. 别人部署到自己的域名后，扩展会连不上。
    // 自定义部署改走 optional_host_permissions，由用户在设置页现场授权。
    const hardcodedPublicHosts = manifest.host_permissions!
      .filter(p => !p.includes('localhost') && !p.includes('127.0.0.1'))
      .filter(p => !VIDEO_HOSTS.some(v => p.includes(v)))
    expect(hardcodedPublicHosts).toEqual([])

    // 自定义公网部署靠它按需申请
    expect(manifest.optional_host_permissions).toBeDefined()
    expect(manifest.optional_host_permissions!.length).toBeGreaterThan(0)

    // content script 覆盖视频站与 OpenNoter 设置页（Cookie 桥）
    expect(matches.some(m => m.includes('bilibili.com'))).toBe(true)
    expect(matches.some(m => m.includes('localhost'))).toBe(true)
  })
})
