<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

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
  type UnderstandTriggerSource,
} from '@/features/tasks/chat.api'
import {
  generatePlan,
  getPlanSummary,
  startPlan,
  type PlanSummaryDto,
} from '@/features/tasks/plans.api'
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
type PlanAction = 'retry_generation' | 'retry_start'
const planAction = ref<PlanAction | null>(null)
const planActionBusy = ref(false)

// AI 请求状态机（request-lifecycle 修复）：模型推理不受普通 CRUD 10s 硬超时限制，
// 慢响应保持「理解中 / 仍在处理」，服务器已持久化成功时不把客户端 transient 错误当失败。
// 'reconciling' = 客户端已断开，正在以服务器事实为准轮询 reconcile。
type AiStatus = 'idle' | 'understanding' | 'reconciling' | 'success' | 'error'
const aiStatus = ref<AiStatus>('idle')
const elapsedSeconds = ref(0)

const understanding = computed(
  () => aiStatus.value === 'understanding' || aiStatus.value === 'reconciling',
)

const POLL_INTERVAL_MS = 3_000
/** reconcile 轮询上限（3s × 40 ≈ 120s，覆盖 Provider 有界 45s + 反代余量）。 */
const MAX_RECONCILE_POLLS = 40
/** Plan 结果不确定时的独立窗口（3s × 45 = 135s）。 */
const MAX_PLAN_RECONCILE_POLLS = 45
/** 慢响应 UX 文案切换阈值。 */
const SLOW_ELAPSED_SECONDS = 10

let elapsedTimer: ReturnType<typeof setInterval> | null = null
let disposed = false
let planController: AbortController | null = null

interface PlanSnapshot {
  planVersion: number | null
  runId: number | null
}

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

async function refreshTaskMeta(): Promise<boolean> {
  try {
    const shell = await getTask(taskId.value)
    if (disposed) return false
    taskVersion.value = shell.version
    currentSpecVersion.value = shell.current_spec_version
    taskState.value = shell.state
    if (shell.current_plan_version) {
      planSummary.value = await getPlanSummary(taskId.value, shell.current_plan_version)
    } else {
      planSummary.value = null
    }
    // Completion Card 由稳定 completion_id 派生渲染（幂等，不追加 Chat 消息）
    if (TERMINAL_STATES.has(shell.state)) {
      completionCard.value = await getCompletion(taskId.value)
    } else {
      completionCard.value = null
    }
    return true
  } catch {
    /* keep last known values */
    return false
  }
}

async function reloadAll(): Promise<void> {
  await Promise.all([loadChat(), refreshTaskMeta()])
}

function snapshotPlanState(): PlanSnapshot {
  return {
    planVersion: planSummary.value?.plan_version ?? null,
    runId: planSummary.value?.run_id ?? null,
  }
}

function hasPlanFactAdvanced(baseline: PlanSnapshot): boolean {
  const current = snapshotPlanState()
  return (
    current.planVersion !== baseline.planVersion ||
    (current.runId !== null && current.runId !== baseline.runId)
  )
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false)
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve(true)
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      resolve(false)
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function reconcilePlanUntilFact(
  baseline: PlanSnapshot,
  signal: AbortSignal,
): Promise<boolean> {
  for (let i = 0; i < MAX_PLAN_RECONCILE_POLLS; i++) {
    if (disposed || !(await abortableDelay(POLL_INTERVAL_MS, signal))) return false
    const refreshed = await refreshTaskMeta()
    if (refreshed && hasPlanFactAdvanced(baseline)) return true
  }
  return false
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
  planController?.abort()
  const controller = new AbortController()
  planController = controller
  planning.value = true
  planAction.value = null
  errorMsg.value = null
  noticeMsg.value = null
  const baseline = snapshotPlanState()
  try {
    await generatePlan(
      taskId.value,
      {
        spec_version: currentSpecVersion.value,
        expected_version: taskVersion.value,
      },
      controller.signal,
    )
    if (disposed) return
    await refreshTaskMeta()
    void loadChat()
  } catch (err) {
    const mapped = mapApiError(err)
    // 组件卸载/导航离开：静默，不把客户端取消误报为失败。
    if (disposed || mapped.kind === 'request_aborted') return

    if (mapped.kind === 'network' || mapped.kind === 'client_timeout') {
      const reconciled = await reconcilePlanUntilFact(baseline, controller.signal)
      if (reconciled) {
        noticeMsg.value = '执行计划结果已从服务器同步。'
        void loadChat()
      } else if (!disposed && !controller.signal.aborted) {
        errorMsg.value = '未确认计划结果，请刷新任务状态后再决定是否重试。'
        planAction.value = 'retry_generation'
      }
      return
    }

    if (mapped.kind === 'execution_preflight_blocked') {
      await refreshTaskMeta()
      errorMsg.value = mapped.message
      planAction.value = null
      return
    }

    if (
      mapped.kind === 'provider_timeout' ||
      mapped.kind === 'plan_generation_timeout' ||
      mapped.kind === 'plan_start_failed'
    ) {
      await refreshTaskMeta()
    }

    if (hasPlanFactAdvanced(baseline) && mapped.kind !== 'plan_start_failed') {
      noticeMsg.value = '执行计划已生成，结果已从服务器同步。'
      void loadChat()
      return
    }

    errorMsg.value = mapped.message
    if (mapped.kind === 'plan_start_failed' && planSummary.value) {
      planAction.value = 'retry_start'
    } else if (mapped.kind === 'provider_timeout' || mapped.kind === 'plan_generation_timeout') {
      planAction.value = 'retry_generation'
    }
  } finally {
    if (planController === controller) {
      planning.value = false
      planController = null
    }
  }
}

async function retryPlanStart(): Promise<void> {
  if (!planSummary.value) return
  planController?.abort()
  const controller = new AbortController()
  planController = controller
  planActionBusy.value = true
  errorMsg.value = null
  try {
    await startPlan(taskId.value, planSummary.value.plan_version, controller.signal)
    if (disposed) return
    await refreshTaskMeta()
    planAction.value = null
    noticeMsg.value = '执行计划启动请求已提交。'
  } catch (err) {
    const mapped = mapApiError(err)
    if (disposed || mapped.kind === 'request_aborted') return
    if (mapped.kind === 'execution_preflight_blocked') {
      await refreshTaskMeta()
      errorMsg.value = mapped.message
      planAction.value = null
      return
    }
    errorMsg.value =
      mapped.kind === 'network'
        ? '启动结果暂未确认；可安全重试启动，请勿重新生成计划。'
        : mapped.message
    planAction.value = 'retry_start'
  } finally {
    if (planController === controller) {
      planActionBusy.value = false
      planController = null
    }
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

function startUnderstanding(): void {
  aiStatus.value = 'understanding'
  errorMsg.value = null
  elapsedSeconds.value = 0
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = setInterval(() => {
    if (disposed) return
    elapsedSeconds.value += 1
  }, 1000)
}

function stopElapsedTimer(): void {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function setReconciling(): void {
  aiStatus.value = 'reconciling'
  stopElapsedTimer()
}

function finishSuccess(): void {
  stopElapsedTimer()
  aiStatus.value = 'success'
}

function finishError(message: string): void {
  stopElapsedTimer()
  aiStatus.value = 'error'
  errorMsg.value = message
}

function finishIdle(): void {
  stopElapsedTimer()
  aiStatus.value = 'idle'
}

function goalResultCount(): number {
  return messages.value.filter((m) => m.ref_type === 'goal_result').length
}

function errorMessageCount(): number {
  return messages.value.filter((m) => m.ref_type === 'error').length
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 以服务器事实为准 reconcile：轮询 Chat 直到出现「本次理解之后」新增的 goal_result /
 * error 消息，或达到有界窗口。返回服务器是否已产生新业务事实。 */
async function reconcileUntilNewFact(
  baselineGoal: number,
  baselineError: number,
): Promise<boolean> {
  for (let i = 0; i < MAX_RECONCILE_POLLS; i++) {
    if (disposed) return false
    if (await reconcileOnce(baselineGoal, baselineError)) return true
    await delay(POLL_INTERVAL_MS)
  }
  return false
}

/** 单次以服务器为准 reconcile（真实网络/Provider 错误路径：快速确认，不长时间轮询）。 */
async function reconcileOnce(baselineGoal: number, baselineError: number): Promise<boolean> {
  if (disposed) return false
  try {
    messages.value = (await getChat(taskId.value)).messages
  } catch {
    return false
  }
  return goalResultCount() > baselineGoal || errorMessageCount() > baselineError
}

const understandingText = computed(() =>
  understanding.value
    ? aiStatus.value === 'reconciling' || elapsedSeconds.value >= SLOW_ELAPSED_SECONDS
      ? '模型仍在处理中，复杂任务可能需要更长时间…'
      : '模型正在理解任务…'
    : '',
)

async function maybeAutoUnderstand(): Promise<void> {
  if (understanding.value || !hasUserMessage() || alreadyUnderstood()) return
  await runUnderstand('AUTO_INITIAL')
}

async function runUnderstand(trigger: UnderstandTriggerSource = 'AUTO_INITIAL'): Promise<void> {
  // 防并发重复理解：一次只允许一个 AI 请求在途（同组件实例）。
  // 跨页面/跨 Tab 的重复由服务器端幂等（understanding_attempts）兜底。
  if (understanding.value) return
  startUnderstanding()
  const baselineGoal = goalResultCount()
  const baselineError = errorMessageCount()
  try {
    const data = await runUnderstanding(taskId.value, trigger)
    if (disposed) return
    if (data.status === 'IN_PROGRESS') {
      // 另一个 attempt 已在途（另一 Tab / reload 竞态）：服务器保证不双跑；
      // 轮询 Chat 直到它落库（或超时窗口）。
      setReconciling()
      const ok = await reconcileUntilNewFact(baselineGoal, baselineError)
      if (ok) {
        finishSuccess()
      } else {
        noticeMsg.value = '模型仍在处理中，请稍后刷新查看。'
        finishIdle()
      }
      return
    }
    if (data.spec_draft) {
      draft.value = asSpecDraftPayload(data.spec_draft)
    }
    try {
      messages.value = (await getChat(taskId.value)).messages
    } catch {
      /* keep current messages */
    }
    void refreshTaskMeta()
    finishSuccess()
  } catch (err) {
    const mapped = mapApiError(err)
    if (disposed) return
    if (mapped.kind === 'model_not_configured') {
      finishIdle()
      openModelRequired()
      return
    }
    // 组件卸载/导航离开：静默，不把客户端取消误报为失败。
    if (mapped.kind === 'request_aborted') {
      finishIdle()
      return
    }
    if (mapped.kind === 'client_timeout') {
      // 浏览器主动放弃等待，但服务器很可能仍在处理：有界 reconcile 轮询，
      // 后端已持久化 goal_result / error 时以服务器为准，不显示“网络请求失败或超时”。
      setReconciling()
      const ok = await reconcileUntilNewFact(baselineGoal, baselineError)
      if (ok) {
        if (goalResultCount() > baselineGoal) {
          noticeMsg.value = '目标理解已完成（处理时间较长，结果已同步）。'
        }
        finishSuccess()
      } else {
        noticeMsg.value = '模型仍在处理中，请稍后刷新查看。'
        finishIdle()
      }
      return
    }
    // 真实网络 / Provider 错误：先做一次以服务器为准的 reconcile，避免隐藏已持久化
    // 的成功；服务器没有新业务事实时才展示错误（不能把所有错误都隐藏）。
    setReconciling()
    const serverFact = await reconcileOnce(baselineGoal, baselineError)
    if (serverFact) {
      finishSuccess()
    } else {
      finishError(mapped.message)
    }
  }
}

async function onSend(content: string): Promise<void> {
  sending.value = true
  try {
    await sendMessage(taskId.value, content)
    messages.value = (await getChat(taskId.value)).messages
    void runUnderstand('USER_SEND')
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

onUnmounted(() => {
  // 组件卸载：停止计时/轮询，避免旧 controller/定时器泄漏影响后续请求。
  disposed = true
  planController?.abort()
  planController = null
  stopElapsedTimer()
})
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
      <p v-if="planSummary?.run_state" class="muted">运行状态：{{ planSummary.run_state }}</p>
      <ul v-if="planSummary?.validator_issues.length" class="plan-issues">
        <li v-for="(issue, index) in planSummary.validator_issues" :key="`${issue.code}-${index}`">
          {{ issue.code }}<span v-if="issue.node_id"> · {{ issue.node_id }}</span>
        </li>
      </ul>
      <CompletionCard v-if="completionCard" :card="completionCard" :task-id="taskId" />
      <ChatMessageList :messages="messages" :loading="loading" />
      <p v-if="understanding" class="muted understanding-status">{{ understandingText }}</p>
      <p v-if="errorMsg" class="chat__error">{{ errorMsg }}</p>
      <p v-if="noticeMsg" class="chat__notice">{{ noticeMsg }}</p>
      <div v-if="planAction || planSummary?.start_recoverable" class="plan-actions">
        <button
          v-if="planAction === 'retry_generation'"
          type="button"
          class="ghost"
          :disabled="planning"
          @click="runPlanGeneration"
        >
          重试生成
        </button>
        <button
          v-if="planAction === 'retry_start' || planSummary?.start_recoverable"
          type="button"
          class="ghost"
          :disabled="planActionBusy"
          @click="retryPlanStart"
        >
          {{ planActionBusy ? '启动中…' : '重试启动' }}
        </button>
      </div>
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
          @click="runUnderstand('USER_REUNDERSTAND')"
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
.plan-issues {
  margin: 0;
  padding-left: 1.25rem;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
}
.plan-actions {
  display: flex;
  gap: 0.5rem;
}
.muted {
  color: var(--color-text-muted, #777);
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
