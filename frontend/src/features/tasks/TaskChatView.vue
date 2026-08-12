<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '@/app/error/ApiError'
import { mapApiError } from '@/app/error/apiErrorMapper'
import { openDrawer } from '@/app/overlay/drawer.store'
import { openModal } from '@/app/overlay/modal.store'
import { parseTaskQuery } from '@/app/router/deepLinks'
import ChatComposer from '@/features/tasks/ChatComposer.vue'
import ChatMessageList from '@/features/tasks/ChatMessageList.vue'
import { getCompletion } from '@/features/artifacts/artifacts.api'
import CompletionCard from '@/features/artifacts/CompletionCard.vue'
import type { CompletionCardView } from '@/features/artifacts/types'
import {
  addSeedUrl,
  confirmSpec,
  getChat,
  getSpecDraft,
  runUnderstanding,
  sendMessage,
  type ChatMessageDto,
} from '@/features/tasks/chat.api'
import { generatePlan, getPlanSummary, type PlanSummaryDto } from '@/features/tasks/plans.api'
import PlanSummaryCard from '@/features/tasks/PlanSummaryCard.vue'
import { asSpecDraftPayload, type SpecDraftPayload } from '@/features/tasks/spec.types'
import { getTask } from '@/features/tasks/tasks.api'
import { createTemplateFromTask } from '@/features/templates/templates.api'
import SpecSummaryCard from '@/features/tasks/SpecSummaryCard.vue'

// Agent 对话工作区（D-031/D-033）。一个 Task = 一个持续 Agent 对话。
// M-06 使用普通 HTTP request/response，不 SSE、不 token 流式。
// Model Required：真正调用 Agent 时才检查模型（D-066），Draft 与输入已持久化，
// 返回 /models 后再回到同一 Task 继续。
// Spec Summary Card（D-035）：draft 存在时展示；「确认并执行」→ confirm（冻结版本）。
const route = useRoute()
const taskId = computed(() => (typeof route.params.taskId === 'string' ? route.params.taskId : ''))

const messages = ref<ChatMessageDto[]>([])
const loading = ref(false)
const sending = ref(false)
const understanding = ref(false)
const confirming = ref(false)
const planning = ref(false)
const urlInput = ref('')
const draft = ref<SpecDraftPayload | null>(null)
const taskVersion = ref<number | null>(null)
const currentSpecVersion = ref<number | null>(null)
const planSummary = ref<PlanSummaryDto | null>(null)
const errorMsg = ref<string | null>(null)
const noticeMsg = ref<string | null>(null)
const taskState = ref<string | null>(null)
const completionCard = ref<CompletionCardView | null>(null)

const TERMINAL_STATES = new Set(['COMPLETED', 'PARTIALLY_COMPLETED', 'CANCELLED'])

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

async function refreshTaskMeta(): Promise<void> {
  try {
    const shell = await getTask(taskId.value)
    taskVersion.value = shell.version
    currentSpecVersion.value = shell.current_spec_version
    taskState.value = shell.state
    if (shell.current_plan_version) {
      planSummary.value = await getPlanSummary(taskId.value, shell.current_plan_version)
    }
    // Completion Card 由稳定 completion_id 派生渲染（幂等，不追加 Chat 消息）
    if (TERMINAL_STATES.has(shell.state)) {
      completionCard.value = await getCompletion(taskId.value)
    } else {
      completionCard.value = null
    }
  } catch {
    /* keep last known values */
  }
}

async function reloadAll(): Promise<void> {
  await Promise.all([loadChat(), refreshTaskMeta()])
}

// Deep Link：/tasks/:taskId/chat?approval=:approvalId → 打开同一个 Approval Drawer（D-057）。
function openApprovalDeepLink(): void {
  const query = parseTaskQuery(route.query)
  if (query.approval) {
    openDrawer('APPROVAL', { approvalId: query.approval })
  }
}

async function runPlanGeneration(): Promise<void> {
  if (!currentSpecVersion.value || taskVersion.value === null) return
  planning.value = true
  errorMsg.value = null
  try {
    await generatePlan(taskId.value, {
      spec_version: currentSpecVersion.value,
      expected_version: taskVersion.value,
    })
    await refreshTaskMeta()
    void loadChat()
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    planning.value = false
  }
}

async function loadChat(): Promise<void> {
  loading.value = true
  try {
    messages.value = (await getChat(taskId.value)).messages
    try {
      draft.value = asSpecDraftPayload((await getSpecDraft(taskId.value)).payload)
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
    draft.value = asSpecDraftPayload(data.spec_draft)
    messages.value = (await getChat(taskId.value)).messages
    void refreshTaskMeta()
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
    draft.value = asSpecDraftPayload(resp.payload)
    urlInput.value = ''
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  }
}

function openSpecEditor(): void {
  openModal('COLLECTION_SPEC_EDITOR', {
    taskId: taskId.value,
    expectedVersion: taskVersion.value ?? 0,
    payload: draft.value,
    onChanged: () => void reloadAll(),
  })
}

async function onSaveAsTemplate(): Promise<void> {
  noticeMsg.value = null
  errorMsg.value = null
  try {
    await createTemplateFromTask(taskId.value)
    noticeMsg.value = '已保存为模板，可在「模板」页查看。'
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  }
}

async function onConfirmSpec(): Promise<void> {
  if (!draft.value) return
  confirming.value = true
  errorMsg.value = null
  try {
    const result = await confirmSpec(taskId.value, taskVersion.value ?? 0, { ...draft.value })
    currentSpecVersion.value = result.spec_version
    await refreshTaskMeta()
    void loadChat()
    // D-038：Spec 确认后自动生成 Plan 并启动合法低风险执行，无二次「确认 Plan」。
    await runPlanGeneration()
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    confirming.value = false
  }
}

watch(
  taskId,
  () => {
    void refreshTaskMeta()
    void loadChat()
    openApprovalDeepLink()
  },
  { immediate: true },
)

watch(
  () => route.query.approval,
  () => openApprovalDeepLink(),
)
</script>

<template>
  <section class="chat">
    <div class="chat__body">
      <SpecSummaryCard
        v-if="draft"
        :payload="draft"
        :confirmed-version="currentSpecVersion"
        :confirming="confirming"
        @open-editor="openSpecEditor"
        @confirm="onConfirmSpec"
        @save-template="onSaveAsTemplate"
      />
      <p v-if="planning" class="muted">正在生成执行计划…</p>
      <PlanSummaryCard v-if="planSummary" :summary="planSummary" />
      <CompletionCard
        v-if="completionCard"
        :card="completionCard"
        :task-id="taskId"
      />
      <ChatMessageList :messages="messages" :loading="loading" />
      <p v-if="errorMsg" class="chat__error">{{ errorMsg }}</p>
      <p v-if="noticeMsg" class="chat__notice">{{ noticeMsg }}</p>
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
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.chat__error {
  color: #c62828;
  font-size: 0.85rem;
}
.chat__notice {
  color: #2e7d32;
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
