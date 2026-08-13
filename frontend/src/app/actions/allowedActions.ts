/** allowed_actions 统一消费（D-067）：按钮显隐/禁用唯一来自后端数组，
 * 前端不在多个组件复制状态机判断。 */
export type TaskAction = string

export function can(action: TaskAction, allowed: readonly TaskAction[]): boolean {
  return allowed.includes(action)
}
