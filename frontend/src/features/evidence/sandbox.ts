/** M-14 第三方 HTML 安全沙箱展示（D-064 安全只读策略）。
 *
 * 绝不使用 v-html/innerHTML 把第三方 HTML 注入主页面 DOM。raw 模式通过
 * sandbox 空属性 iframe + 注入严格 CSP meta 展示：禁止脚本、禁止网络、
 * 禁止 form submit、opaque origin。
 */

const CSP_META =
  `<meta http-equiv="Content-Security-Policy" content="` +
  `default-src 'none'; img-src data:; style-src 'unsafe-inline'; ` +
  `base-uri 'none'; form-action 'none'">`

export function buildSandboxHtml(rawHtml: string): string {
  if (/<head[\s>]/i.test(rawHtml)) {
    return rawHtml.replace(/<head([^>]*)>/i, `<head$1>${CSP_META}`)
  }
  return `<!DOCTYPE html><html><head>${CSP_META}</head><body>${rawHtml}</body></html>`
}
