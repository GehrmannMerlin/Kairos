import { onBeforeUnmount, onMounted } from 'vue'

/** Overlay 通用：Escape 关闭。 */
export function useEscapeClose(close: () => void): void {
  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') close()
  }
  onMounted(() => window.addEventListener('keydown', onKeydown))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
}
