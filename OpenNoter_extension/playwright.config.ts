import { defineConfig } from '@playwright/test'

// Test the packaged MV3 extension; no live backend or Vite dev server is needed.
export default defineConfig({
  testDir: './e2e',
  retries: 0,
  workers: 1,
  use: { headless: true },
})
