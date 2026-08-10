<script setup lang="ts">
import type { ChatMessageDto } from '@/features/tasks/chat.api'

// 纯展示：append-only 消息列表。结构化消息（goal_result / error 等）保留
// ref_type/ref_id/meta 供上层按需渲染卡片，不把业务事实压成纯文本。
defineProps<{ messages: ChatMessageDto[]; loading?: boolean }>()
</script>

<template>
  <div class="chat-messages" role="log" aria-live="polite">
    <div v-if="loading" class="muted">加载中…</div>
    <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
      <div class="msg__bubble">
        <span v-if="m.ref_type" class="msg__tag">{{ m.ref_type }}</span>
        <p class="msg__content">{{ m.content }}</p>
        <p v-if="m.role === 'assistant' && m.meta?.duration_ms" class="msg__meta">
          模型 {{ String(m.meta.provider ?? '') }} · {{ String(m.meta.duration_ms) }}ms
        </p>
      </div>
    </div>
    <p v-if="!loading && messages.length === 0" class="empty">
      还没有消息，描述你的采集需求开始协作。
    </p>
  </div>
</template>

<style scoped>
.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.msg {
  display: flex;
}
.msg--user {
  justify-content: flex-end;
}
.msg--assistant,
.msg--system {
  justify-content: flex-start;
}
.msg__bubble {
  max-width: 78%;
  padding: 0.6rem 0.9rem;
  border-radius: 10px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
}
.msg--user .msg__bubble {
  background: var(--color-text);
  color: var(--color-bg);
  border-color: var(--color-text);
}
.msg__tag {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  display: block;
  margin-bottom: 0.25rem;
}
.msg__content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg__meta {
  margin: 0.4rem 0 0;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
</style>
