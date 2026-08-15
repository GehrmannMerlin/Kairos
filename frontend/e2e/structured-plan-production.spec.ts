import { expect, test } from '@playwright/test'

const SHANDONG_PROMPT = '采集山东省人民政府官网发布的最近一个月的干部任前公示信息'
const externalSmokeEnabled = process.env.KAIROS_E2E_EXTERNAL_SMOKE === '1'

test.describe('Production structured plan smoke', () => {
  test.skip(
    !externalSmokeEnabled,
    'set KAIROS_E2E_EXTERNAL_SMOKE=1 and the KAIROS_E2E_* credentials to run externally',
  )

  test('real DeepSeek plan is persisted and workflow start is visible', async ({ page }) => {
    const baseURL = process.env.KAIROS_E2E_BASE_URL
    const email = process.env.KAIROS_E2E_EMAIL
    const password = process.env.KAIROS_E2E_PASSWORD
    if (!baseURL || !email || !password) {
      throw new Error('KAIROS_E2E_BASE_URL, KAIROS_E2E_EMAIL and KAIROS_E2E_PASSWORD are required')
    }

    await page.goto('/login')
    await page.getByLabel('邮箱').fill(email)
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page).toHaveURL(/\/app$/, { timeout: 20_000 })

    await page.getByPlaceholder(/描述你的采集需求/).fill(SHANDONG_PROMPT)
    await page.getByRole('button', { name: '开始采集' }).click()
    await expect(page).toHaveURL(/\/tasks\/\d+\/chat$/, { timeout: 20_000 })

    const confirm = page.getByRole('button', { name: '确认并执行' })
    await expect(confirm).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('推理请求失败')).toHaveCount(0)
    await confirm.click()

    await expect(page.locator('[data-test="plan-summary"]')).toBeVisible({ timeout: 150_000 })
    await expect(page.getByText(/运行状态：(pending|running)/)).toBeVisible()
    await expect(page.getByText('推理请求失败')).toHaveCount(0)
  })
})
