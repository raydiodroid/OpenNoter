import { expect, test } from './fixtures'

test.beforeEach(async ({ context }) => {
  await context.route('http://localhost:8483/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const data = path === '/api/auth/me'
      ? { id: 1, is_superuser: false }
      : path === '/api/models'
        ? [{ id: 'own', provider_id: 'own:llm', model_name: 'my-model', source: 'own' }]
        : path === '/api/me/credentials'
          ? { mode: 'optional', credentials: [] }
          : { cookie_mode: 'optional' }
    await route.fulfill({ json: { code: 0, data } })
  })
})

test('popup shows real product controls', async ({ page, extensionId }) => {
  await page.goto(`chrome-extension://${extensionId}/dist/popup/index.html`)
  await expect(page.getByText('OpenNoter', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '设置', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成笔记', exact: true })).toBeDisabled()
})

test('ordinary users see personal credentials and no administrator tabs', async ({ page, extensionId }) => {
  await page.goto(`chrome-extension://${extensionId}/dist/options/index.html`)
  await expect(page.getByRole('heading', { name: '通用', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '模型供应商', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '我的凭据', exact: true }).click()
  await expect(page.getByRole('heading', { name: '我的模型凭据' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '音频转写', exact: true })).toBeVisible()
})

test('editing connection settings preserves focus across scope changes', async ({ page, extensionId }) => {
  await page.goto(`chrome-extension://${extensionId}/dist/options/index.html`)
  const url = page.getByPlaceholder('http://localhost:8483', { exact: true })
  await url.fill('http://localhost:8483')
  await url.focus()
  await page.keyboard.press('End')
  await page.keyboard.type('/test', { delay: 80 })
  await expect(url).toHaveValue('http://localhost:8483/test')
  await expect(url).toBeFocused()
})
