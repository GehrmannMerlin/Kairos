import { describe, expect, it } from 'vitest'

import { buildSandboxHtml } from './sandbox'
import { findInSnapshotHtml } from './locator'

describe('findInSnapshotHtml 定位（基于已保存快照，不请求当前网页）', () => {
  const html = `
    <html><body>
      <table id="biz"><tbody>
        <tr class="row"><td>上海自动化设备有限公司</td></tr>
      </tbody></table>
    </body></html>
  `

  it('CSS selector 命中返回片段', () => {
    const result = findInSnapshotHtml(html, 'table#biz tr td')
    expect(result.found).toBe(true)
    expect(result.snippet).toContain('上海自动化设备有限公司')
  })

  it('#id 兜底命中', () => {
    const result = findInSnapshotHtml(html, '#biz')
    expect(result.found).toBe(true)
  })

  it('locator 失效返回 fallback（found=false）', () => {
    const result = findInSnapshotHtml(html, 'table#none tr td')
    expect(result.found).toBe(false)
    expect(result.snippet).toBe('')
  })

  it('空 locator / 空 html 不抛错', () => {
    expect(findInSnapshotHtml(html, null).found).toBe(false)
    expect(findInSnapshotHtml('', 'table').found).toBe(false)
  })
})

describe('buildSandboxHtml 安全只读策略', () => {
  it('注入严格 CSP meta（禁止脚本/网络）', () => {
    const out = buildSandboxHtml('<html><body>历史</body></html>')
    expect(out).toContain('Content-Security-Policy')
    expect(out).toContain("default-src 'none'")
    expect(out).toContain('form-action')
  })

  it('无 <head> 时包装成完整文档', () => {
    const out = buildSandboxHtml('<p>内容</p>')
    expect(out).toContain('<head>')
    expect(out).toContain('<p>内容</p>')
  })
})
