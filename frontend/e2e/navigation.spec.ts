import { expect, test, type Page } from '@playwright/test'

const USER = {
  id: 1,
  email: 'alice@example.com',
  display_name: null,
  created_at: '2026-08-10T00:00:00Z',
}

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

/** 拦截后端 /api/*（根路径前缀），返回脚本化响应，E2E 不依赖真实后端。
 * 注意不能用宽泛的 api 双星 glob：那会把 /src/app/api/*.ts 源文件请求也匹配并 404。 */
async function mockApi(page: Page, opts: { authed?: boolean; taskId?: number } = {}): Promise<void> {
  await page.route((url) => url.pathname.startsWith('/api/'), (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (path === '/api/auth/me') {
      if (opts.authed) {
        return route.fulfill(json(USER))
      }
      return route.fulfill(json({ detail: { code: 'AUTH_REQUIRED', message: '请先登录' } }, 401))
    }
    if (path === '/api/auth/login' && method === 'POST') {
      return route.fulfill(
        json({
          user: USER,
          session: {
            id: 1,
            created_at: '2026-08-10T00:00:00Z',
            expires_at: '2026-08-17T00:00:00Z',
            revoked_at: null,
            is_current: true,
          },
        }),
      )
    }
    if (path === '/api/tasks' && method === 'GET') {
      return route.fulfill(json({ tasks: [] }))
    }
    if (opts.taskId && path === `/api/tasks/${opts.taskId}` && method === 'GET') {
      return route.fulfill(
        json({
          task_id: opts.taskId,
          title: '采集深圳供应商',
          state: 'DRAFT',
          version: 1,
          task_type: 'directed',
          current_spec_version: null,
          current_plan_version: null,
          allowed_actions: ['submit', 'delete'],
          created_at: '2026-08-10T00:00:00Z',
          updated_at: '2026-08-10T00:00:00Z',
        }),
      )
    }
    if (path.startsWith('/api/providers/')) {
      return route.fulfill(
        json({ configs: [], definitions: [], models: [], searches: [] }),
      )
    }
    return route.fulfill(json({ detail: { code: 'NOT_FOUND', message: '资源不存在' } }, 404))
  })
}

function collectPageErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (err) => errors.push(err.message))
  return errors
}

test.describe('基础导航 E2E', () => {
  test('A: 未登录访问 /app 重定向 /login，登录后进入 /app', async ({ page }) => {
    const errors = collectPageErrors(page)
    await mockApi(page, { authed: false })

    await page.goto('/app')
    await expect(page).toHaveURL(/\/login/)

    await page.getByLabel('邮箱').fill('alice@example.com')
    await page.getByLabel('密码').fill('password123')
    await page.getByRole('button', { name: '登录' }).click()

    await expect(page).toHaveURL(/\/app/)
    await expect(page.getByText('任务创建能力将在下一模块接入')).toBeVisible()
    expect(errors).toEqual([])
  })

  test('B: 登录后经 Sidebar 导航五页，无 JS fatal error', async ({ page }) => {
    const errors = collectPageErrors(page)
    await mockApi(page, { authed: true })

    await page.goto('/app')
    await expect(page).toHaveURL(/\/app/)

    const nav = [
      { link: '我的任务', path: '/tasks', heading: '我的任务' },
      { link: '模板', path: '/templates', heading: '模板' },
      { link: '模型配置', path: '/models', heading: '模型与搜索服务配置' },
      { link: '设置', path: '/settings', heading: '设置' },
    ]
    for (const item of nav) {
      await page.getByRole('link', { name: item.link, exact: true }).click()
      await expect(page).toHaveURL(new RegExp(`${item.path}$`))
      await expect(page.getByRole('heading', { name: item.heading }).first()).toBeVisible()
    }
    expect(errors).toEqual([])
  })

  test('C: owner-safe Task fixture 仅三 Tab + 无权限 Task 不泄漏 metadata', async ({ page }) => {
    const errors = collectPageErrors(page)
    await mockApi(page, { authed: true, taskId: 1 })

    await page.goto('/tasks/1/chat')
    await expect(page.getByText('采集深圳供应商')).toBeVisible()

    const tabs = page.locator('.task-shell__tab')
    await expect(tabs).toHaveCount(3)
    await expect(tabs.nth(0)).toHaveText('对话')
    await expect(tabs.nth(1)).toHaveText('数据')
    await expect(tabs.nth(2)).toHaveText('质量')

    await page.getByRole('link', { name: '数据' }).click()
    await expect(page).toHaveURL(/\/tasks\/1\/data/)
    await page.getByRole('link', { name: '质量' }).click()
    await expect(page).toHaveURL(/\/tasks\/1\/quality/)
    await page.goto('/tasks/1/execution')
    await expect(page).toHaveURL(/\/tasks\/1\/execution/)
    await expect(page.getByText('暂无执行记录')).toBeVisible()

    // 无权限 Task → 通用 not-found，不出现 fixture 标题。
    await page.goto('/tasks/999/chat')
    await expect(page.getByText('任务不存在或无权访问')).toBeVisible()
    await expect(page.getByText('采集深圳供应商')).toHaveCount(0)
    expect(errors).toEqual([])
  })
})
