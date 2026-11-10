import type { AsrProvider } from '../src/logic/types'
import { expect, test } from './fixtures'

test('administrator manages ASR providers without changing the default implicitly', async ({ context, page, extensionId }) => {
  const providers: AsrProvider[] = [{ id: 'groq', name: 'Existing ASR', engine: 'groq', logo: 'groq', base_url: 'https://existing.example/v1', model_name: 'whisper', enabled: true, builtin: true, has_api_key: true }]
  const config = { transcriber_type: 'groq', whisper_model_size: 'base', providers, available_types: [], whisper_model_sizes: ['base'], mlx_whisper_available: false }
  const updates: Record<string, unknown>[] = []
  await context.route('http://localhost:8483/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let data: unknown = {}
    if (path === '/api/auth/me') {
      data = { id: 1, is_superuser: true }
    }
    else if (path === '/api/me/credentials') {
      data = { mode: 'shared', credentials: [] }
    }
    else if (path === '/api/models') {
      data = []
    }
    else if (path === '/api/transcriber_models_status') {
      data = { whisper: [], mlx_whisper: [], mlx_available: false }
    }
    else if (path === '/api/transcriber_config') {
      if (request.method() === 'POST')
        Object.assign(config, request.postDataJSON())
      data = config
    }
    else if (path === '/api/asr/providers' && request.method() === 'POST') {
      const { api_key, ...fields } = request.postDataJSON()
      data = { ...fields, id: 'asr_test', engine: 'groq', enabled: true, builtin: false, has_api_key: Boolean(api_key) }
      providers.push(data as AsrProvider)
    }
    else if (path.startsWith('/api/asr/providers/')) {
      const index = providers.findIndex(p => p.id === path.split('/').pop())
      if (request.method() === 'DELETE') {
        providers.splice(index, 1)
      }
      else {
        const update = request.postDataJSON()
        updates.push(update)
        Object.assign(providers[index], update)
        data = providers[index]
      }
    }
    await route.fulfill({ json: { code: 0, data } })
  })
  await page.goto(`chrome-extension://${extensionId}/dist/options/index.html`)
  await page.getByRole('button', { name: '音频转写配置', exact: false }).click()
  await page.getByRole('button', { name: '添加供应商', exact: true }).click()
  await page.getByLabel('供应商名称', { exact: true }).fill('My ASR')
  await page.getByLabel('图标', { exact: true }).selectOption('server')
  await page.getByLabel('API 地址', { exact: true }).fill('https://new.example/v1')
  await page.getByLabel('API Key', { exact: true }).fill('test-key')
  await page.getByLabel('模型名称', { exact: true }).fill('sensevoice')
  await page.getByRole('button', { name: '添加供应商', exact: true }).last().click()
  await expect(page.getByRole('heading', { name: 'My ASR', exact: true })).toBeVisible()
  expect(config.transcriber_type).toBe('groq')
  expect(providers[1].logo).toBe('server')
  await page.getByRole('switch', { name: '启用 My ASR', exact: true }).click()
  await expect(page.getByRole('button', { name: '设为默认', exact: true })).toBeDisabled()
  await page.getByRole('switch', { name: '启用 My ASR', exact: true }).click()
  await expect(page.getByRole('button', { name: '设为默认', exact: true })).toBeEnabled()
  await page.getByLabel('模型名称', { exact: true }).fill('sensevoice-v2')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByRole('button', { name: '保存修改', exact: true })).toBeDisabled()
  expect(updates.at(-1)).not.toHaveProperty('api_key')
  await page.getByRole('button', { name: '设为默认', exact: true }).click()
  await expect(page.getByRole('button', { name: '默认使用', exact: true })).toBeVisible()
  expect(config.transcriber_type).toBe('asr_test')
  await expect(page.getByRole('button', { name: '删除供应商', exact: true })).toBeDisabled()
})
