import { defineConfig } from '@playwright/test'

const externalBaseURL = process.env.KAIROS_E2E_BASE_URL

export default defineConfig({
  testDir: './e2e',
  timeout: externalBaseURL ? 180_000 : 30_000,
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: externalBaseURL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command: 'npm run dev',
        url: 'http://localhost:5173',
        reuseExistingServer: true,
        timeout: 60_000,
      },
})
