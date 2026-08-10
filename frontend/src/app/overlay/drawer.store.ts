import { ref } from 'vue'

/** D-067 Overlay Drawer 类型：全部为 Overlay，不挤压底层布局。 */
export const DRAWER_TYPES = [
  'TASK_STATUS',
  'APPROVAL',
  'CREDENTIAL',
  'RECORD',
  'EVIDENCE_QUICK',
  'NODE_DETAIL',
  'PROVIDER_EDIT',
] as const

export type DrawerType = (typeof DRAWER_TYPES)[number]

export interface DrawerState {
  open: boolean
  type: DrawerType | null
  payload: unknown
}

const state = ref<DrawerState>({ open: false, type: null, payload: undefined })

export function openDrawer(type: DrawerType, payload?: unknown): void {
  state.value = { open: true, type, payload }
}

export function closeDrawer(): void {
  state.value = { open: false, type: null, payload: undefined }
}

export function toggleDrawer(type: DrawerType, payload?: unknown): void {
  if (state.value.open && state.value.type === type) {
    closeDrawer()
  } else {
    openDrawer(type, payload)
  }
}

export const drawerState = state
