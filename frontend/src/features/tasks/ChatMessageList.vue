<script setup lang="ts">
import ChatApprovalCard from '@/features/tasks/ChatApprovalCard.vue'
import ChatCredentialCard from '@/features/tasks/ChatCredentialCard.vue'
import type { ChatMessageDto } from '@/features/tasks/chat.api'

// 纯展示：append-only 消息列表。结构化消息（goal_result / error / plan / approval /
// credential_required 等）保留 ref_type/ref_id/meta 供上层按需渲染卡片，不把业务事实压成纯文本。
defineProps<{ messages: ChatMessageDto[]; loading?: boolean }>()

function taskIdOf(m: ChatMessageDto): string | number {
  const t = m.meta?.task_id
  return typeof t === 'number' || typeof t === 'string' ? t : ''
}

function domainOf(m: ChatMessageDto): string | undefined {
  return m.meta?.domain ? String(m.meta.domain) : undefined
}
</script>

<template>
  <div class="chat-messages" role="log" aria-live="polite">
    <div v-if="loading" class="muted">加载中…</div>
    <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
      <div v-if="m.ref_type === 'approval' && m.ref_id" class="msg__bubble">
        <ChatApprovalCard :approval-id="m.ref_id" />
      </div>
      <div v-else-if="m.ref_type === 'credential_required'" class="msg__bubble">
        <ChatCredentialCard :task-id="taskIdOf(m)" :domain="domainOf(m)" />
      </div>
      <div v-else class="msg__bubble">
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
