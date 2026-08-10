<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

const error = ref<string | null>(null)

onErrorCaptured((err, _instance, info) => {
  error.value = `${err instanceof Error ? err.message : String(err)} (${info})`
  return false
})
</script>

<template>
  <div v-if="error" class="error-boundary" data-testid="error-boundary">
    <h2>页面渲染出错</h2>
    <p class="error-boundary__message">{{ error }}</p>
    <button @click="error = null">重试</button>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  padding: 2rem;
  border: 1px solid var(--color-danger);
  border-radius: 8px;
  margin: 1.5rem;
}
.error-boundary__message {
  color: var(--color-text-secondary);
  word-break: break-word;
}
</style>
