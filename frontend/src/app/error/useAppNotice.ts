import { ref } from 'vue'

export interface AppNotice {
  id: number
  kind: 'info' | 'error' | 'success'
  message: string
}

/** 轻量全局通知层；AppShell 可挂载显示，供全局错误映射消费。 */
export const appNotices = ref<AppNotice[]>([])

let nextId = 0

export function pushNotice(message: string, kind: AppNotice['kind'] = 'info'): void {
  appNotices.value.push({ id: ++nextId, kind, message })
}

export function dismissNotice(id: number): void {
  appNotices.value = appNotices.value.filter((n) => n.id !== id)
}

export function clearNotices(): void {
  appNotices.value = []
}
