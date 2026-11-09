import { Buffer } from 'node:buffer'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { r } from '../../scripts/utils'
import { getManifest } from '../manifest'

const iconSizes = [16, 32, 48, 128] as const
const iconPaths = Object.fromEntries(iconSizes.map(size => [size, `assets/icon-${size}.png`]))

function pngSize(path: string) {
  const bytes = readFileSync(path)
  expect(bytes.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)]
}

describe('opennoter brand assets', () => {
  it('uses matching PNG sizes for extension and toolbar icons', async () => {
    const manifest = await getManifest()
    expect(manifest.icons).toEqual(iconPaths)
    expect(manifest.action?.default_icon).toEqual(iconPaths)
    for (const size of [...iconSizes, 512])
      expect(pngSize(r(`extension/assets/icon-${size}.png`))).toEqual([size, size])
  })

  it('keeps the frontend, extension, favicon and documentation SVGs identical', () => {
    const logo = readFileSync(r('src/assets/logo.svg'), 'utf8')
    expect(logo).toContain('viewBox="0 0 512 512"')
    expect(logo).toContain('id="blue-pen"')
    for (const path of [
      'extension/assets/icon.svg',
      '../OpenNoter_frontend/src/assets/icon.svg',
      '../OpenNoter_frontend/public/icon.svg',
      '../doc/icon.svg',
    ])
      expect(readFileSync(r(path), 'utf8')).toBe(logo)
  })

  it('provides square desktop PNG and native macOS/Windows icons', () => {
    expect(pngSize(r('../OpenNoter_frontend/src-tauri/icons/icon.png'))).toEqual([512, 512])
    expect(readFileSync(r('../OpenNoter_frontend/src-tauri/icons/icon.icns')).subarray(0, 4).toString()).toBe('icns')
    expect(readFileSync(r('../OpenNoter_frontend/src-tauri/icons/icon.ico')).subarray(0, 4)).toEqual(Buffer.from([0, 0, 1, 0]))
    const config = JSON.parse(readFileSync(r('../OpenNoter_frontend/src-tauri/tauri.conf.json'), 'utf8'))
    expect(config.bundle.icon).toEqual(expect.arrayContaining(['icons/icon.icns', 'icons/icon.ico', 'icons/icon.png']))
  })
})
