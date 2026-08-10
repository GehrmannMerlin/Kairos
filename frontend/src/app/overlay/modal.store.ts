import { ref } from 'vue'

/** D-067 Modal/Sheet 类型；COLLECTION_SPEC_EDITOR 与 TEMPLATE_VARIABLES 走 Sheet。 */
export const MODAL_TYPES = [
  'COLLECTION_SPEC_EDITOR',
  'TEMPLATE_VARIABLES',
  'EXPORT',
  'DELETE_CONFIRM',
  'MODEL_REQUIRED',
] as const

export type ModalType = (typeof MODAL_TYPES)[number]

export interface ModalState {
  open: boolean
  type: ModalType | null
  payload: unknown
}

const state = ref<ModalState>({ open: false, type: null, payload: undefined })

export function openModal(type: ModalType, payload?: unknown): void {
  state.value = { open: true, type, payload }
}

export function closeModal(): void {
  state.value = { open: false, type: null, payload: undefined }
}

export const modalState = state
