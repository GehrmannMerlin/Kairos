<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  sending?: boolean
  placeholder?: string
  disabled?: boolean
}>()

const emit = defineEmits<{ submit: [content: string] }>()

const text = ref('')

function send(): void {
  const value = text.value.trim()
  if (!value || props.sending || props.disabled) return
  emit('submit', value)
  text.value = ''
}
</script>

<template>
  <form class="composer" @submit.prevent="send">
    <textarea
      v-model="text"
      class="composer__input"
      rows="2"
      :placeholder="placeholder ?? '继续描述或补充信息…'"
      :disabled="sending || disabled"
    />
    <button type="submit" class="composer__send" :disabled="sending || disabled || !text.trim()">
      {{ sending ? '发送中…' : '发送' }}
    </button>
  </form>
</template>

<style scoped>
.composer {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}
.composer__input {
  flex: 1;
  padding: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  resize: vertical;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}
.composer__send {
  padding: 0.55rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
.composer__send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
