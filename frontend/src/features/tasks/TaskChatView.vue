<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '@/app/error/ApiError'
import { mapApiError } from '@/app/error/apiErrorMapper'
import { openModal } from '@/app/overlay/modal.store'
import ChatComposer from '@/features/tasks/ChatComposer.vue'
import ChatMessageList from '@/features/tasks/ChatMessageList.vue'
import {
  addSeedUrl,
  getChat,
  getSpecDraft,
  runUnderstanding,
  sendMessage,
  type ChatMessageDto,
  type SpecDraftResponse,
} from '@/features/tasks/chat.api'

// Agent 对话工作区（D-031/D-033）。一个 Task = 一个持续 Agent 对话。
// M-06 使用普通 HTTP request/response，不 SSE、不 token 流式。
// Model Required：真正调用 Agent 时才检查模型（D-066），Draft 与输入已持久化，
// 返回 /models 后再回到同一 Task 继续。
const route = useRoute()
const taskId = computed(() => (typeof route.params.taskId === 'string' ? route.params.taskId : ''))

const messages = ref<ChatMessageDto[]>([])
const loading = ref(false)
const sending = ref(false)
const understanding = ref(false)
const urlInput = ref('')
const draft = ref<SpecDraftResponse['payload']>(null)
const errorMsg = ref<string | null>(null)

function hasUserMessage(): boolean {
  return messages.value.some((m) => m.role === 'user')
}

function alreadyUnderstood(): boolean {
  return messages.value.some(
    (m) => m.role === 'assistant' && (m.ref_type === 'goal_result' || m.ref_type === 'error'),
  )
}

function openModelRequired(): void {
  openModal('MODEL_REQUIRED', { returnTo: `/tasks/${taskId.value}/chat` })
}

async function loadChat(): Promise<void> {
  loading.value = true
  try {
    messages.value = (await getChat(taskId.value)).messages
    try {
      draft.value = (await getSpecDraft(taskId.value)).payload
    } catch {
      draft.value = null
    }
    // 目标理解必须在 chat 加载完成后触发，避免与首次加载竞争。
    void maybeAutoUnderstand()
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function maybeAutoUnderstand(): Promise<void> {
  if (understanding.value || !hasUserMessage() || alreadyUnderstood()) return
  await runUnderstand()
}

async function runUnderstand(): Promise<void> {
  understanding.value = true
  errorMsg.value = null
  try {
    const data = await runUnderstanding(taskId.value)
    draft.value = data.spec_draft
    messages.value = (await getChat(taskId.value)).messages
  } catch (err) {
    if (err instanceof ApiError && mapApiError(err).kind === 'model_not_configured') {
      openModelRequired()
    } else {
      // Backend already persisted a recoverable error message; reload to show it.
      try {
        messages.value = (await getChat(taskId.value)).messages
      } catch {
        /* keep current messages */
      }
      errorMsg.value = err instanceof Error ? err.message : String(err)
    }
  } finally {
    understanding.value = false
  }
}

async function onSend(content: string): Promise<void> {
  sending.value = true
  try {
    await sendMessage(taskId.value, content)
    messages.value = (await getChat(taskId.value)).messages
    void runUnderstand()
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

async function onAddUrl(): Promise<void> {
  const url = urlInput.value.trim()
  if (!url) return
  try {
    const resp = await addSeedUrl(taskId.value, url)
    draft.value = resp.payload
    urlInput.value = ''
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  }
}

watch(taskId, () => void loadChat(), { immediate: true })
</script>

<template>
  <section class="chat">
    <div class="chat__body">
      <ChatMessageList :messages="messages" :loading="loading" />
      <p v-if="errorMsg" class="chat__error">{{ errorMsg }}</p>
    </div>

    <div class="chat__footer">
      <div class="chat__urlrow">
        <input
          v-model="urlInput"
          class="chat__url"
          type="text"
          placeholder="添加网址（只写入采集方案，不会立即抓取）"
        />
        <button type="button" class="ghost" @click="onAddUrl">添加网址</button>
      </div>
      <div class="chat__actions">
        <button
          v-if="hasUserMessage()"
          type="button"
          class="ghost"
          :disabled="understanding"
          @click="runUnderstand"
        >
          {{ understanding ? '理解中…' : '重新理解' }}
        </button>
        <ChatComposer :sending="sending" @submit="onSend" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-height: 40vh;
}
.chat__body {
  flex: 1;
}
.chat__error {
  color: #c62828;
  font-size: 0.85rem;
}
.chat__footer {
  border-top: 1px solid var(--color-border);
  padding-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.chat__urlrow {
  display: flex;
  gap: 0.5rem;
}
.chat__url {
  flex: 1;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.85rem;
}
.chat__actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
button.ghost {
  padding: 0.5rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.85rem;
}
button.ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
