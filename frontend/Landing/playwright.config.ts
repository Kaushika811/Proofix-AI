import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  workers: 2,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:4178',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4178 --strictPort',
    url: 'http://127.0.0.1:4178',
    reuseExistingServer: !process.env.CI
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 }
      }
    },
    {
      name: 'mobile',
      use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' }
    },
    {
      name: 'reduced-motion',
      use: {
        ...devices['Desktop Chrome'],
        reducedMotion: 'reduce',
        viewport: { width: 1280, height: 720 }
      }
    }
  ]
})
