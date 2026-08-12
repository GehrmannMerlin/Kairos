/** M-14 证据定位（D-064）：在"当时保存的 Snapshot"内用 locator 定位。
 *
 * 使用 DOMParser 在父文档解析快照 HTML（DOMParser 不执行脚本，安全），
 * CSS selector 优先，#id 兜底；找不到 → fallback。绝不请求当前网页。
 */

import type { LocateResult } from './types'

export function findInSnapshotHtml(html: string, locator: string | null): LocateResult {
  if (!html || !locator) {
    return { found: false, snippet: '' }
  }
  const doc = new DOMParser().parseFromString(html, 'text/html')
  let el: Element | null = null
  try {
    el = doc.querySelector(locator)
  } catch {
    el = null
  }
  if (!el) {
    const id = locator.replace(/^#/, '')
    if (id && id !== locator) {
      el = doc.getElementById(id)
    }
  }
  if (!el) {
    return { found: false, snippet: '' }
  }
  const snippet = (el.textContent ?? el.outerHTML ?? '').trim().slice(0, 300)
  return { found: true, snippet }
}
