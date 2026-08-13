<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  approveApproval,
  getApproval,
  rejectApproval,
  revokeApproval,
  type ApprovalDto,
} from '@/features/tasks/approvals.api'

// Approval Drawer（D-057 / M-08）。展示真实审批状态与操作；
// Deep Link /tasks/:taskId/chat?approval=:id 打开的是同一个 Drawer，不新建对象。
// 不显示预计费用/人民币/美元（D-036）。
const props = defineProps<{ payload?: unknown }>()
const payload = (props.payload ?? {}) as { approvalId?: number | string }
const approvalId = computed(() => payload.approvalId)

const approval = ref<ApprovalDto | null>(null)
const loading = ref(false)
const busy = ref(false)
const notice = ref('')
const failed = ref(false)

const scopeLabel = computed(() => {
  const map: Record<string, string> = {
    this_action: '仅本次动作',
    same_parameters_batch: '参数相同的批次',
    task_scoped_limited: '任务内限定范围',
  }
  return approval.value ? (map[approval.value.approved_scope] ?? approval.value.approved_scope) : ''
})

async function load(): Promise<void> {
  if (!approvalId.value) return
  loading.value = true
  failed.value = false
  try {
    approval.value = await getApproval(approvalId.value)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function run(command: 'approve' | 'reject' | 'revoke'): Promise<void> {
  if (!approval.value || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const cmd = { expected_version: 1 }
    approval.value =
      command === 'approve'
        ? await approveApproval(approval.value.approval_id, cmd)
        : command === 'reject'
          ? await rejectApproval(approval.value.approval_id, cmd)
          : await revokeApproval(approval.value.approval_id, cmd)
  } catch (err) {
    notice.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div v-if="failed" class="muted">该审批不存在或无权访问</div>
  <div v-else-if="loading" class="muted">加载中…</div>
  <div v-else-if="approval" class="approval-drawer">
    <dl class="approval-list">
      <div class="row">
        <dt>状态</dt>
        <dd data-test="state">{{ approval.state }}</dd>
      </div>
      <div class="row">
        <dt>动作</dt>
        <dd>{{ approval.action_type }}</dd>
      </div>
      <div class="row">
        <dt>目标</dt>
        <dd class="wrap">{{ approval.target ?? '—' }}</dd>
      </div>
      <div class="row">
        <dt>原因</dt>
        <dd class="wrap">{{ approval.reason ?? '—' }}</dd>
      </div>
      <div class="row">
        <dt>授权范围</dt>
        <dd>{{ scopeLabel }}</dd>
      </div>
      <div v-if="approval.credential_ref" class="row">
        <dt>凭据</dt>
        <dd class="wrap">{{ String(approval.credential_ref.masked ?? '') }}</dd>
      </div>
      <div v-if="approval.status_payload" class="row">
        <dt>副作用</dt>
        <dd class="wrap">{{ JSON.stringify(approval.status_payload) }}</dd>
      </div>
      <div class="row">
        <dt>有效期</dt>
        <dd>{{ approval.expires_at ?? '—' }}</dd>
      </div>
    </dl>

    <div v-if="approval.state === 'PENDING'" class="approval-actions">
      <button
        type="button"
        class="primary"
        :disabled="busy"
        data-test="approve"
        @click="run('approve')"
      >
        批准
      </button>
      <button
        type="button"
        class="danger"
        :disabled="busy"
        data-test="reject"
        @click="run('reject')"
      >
        拒绝
      </button>
    </div>
    <button
      v-if="approval.state === 'PENDING' || approval.state === 'APPROVED'"
      type="button"
      class="ghost"
      :disabled="busy"
      @click="run('revoke')"
    >
      撤销（未消费）
    </button>
    <p v-if="notice" class="error">{{ notice }}</p>
  </div>
</template>

<style scoped>
.approval-list {
  margin: 0 0 1.25rem;
}
.row {
  display: flex;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px dashed var(--color-border);
}
.row dt {
  width: 5rem;
  flex: none;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.row dd {
  margin: 0;
  font-size: 0.85rem;
}
.wrap {
  word-break: break-word;
}
.approval-actions {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
button.primary,
button.danger,
button.ghost {
  padding: 0.5rem 0.9rem;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
  font-size: 0.85rem;
}
button.primary {
  background: var(--color-text);
  color: var(--color-bg);
  border: 1px solid var(--color-text);
}
button.danger {
  background: transparent;
  color: var(--color-danger, #c0392b);
  border: 1px solid var(--color-danger, #c0392b);
}
button.ghost {
  background: transparent;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.muted {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.error {
  color: var(--color-danger, #c0392b);
  font-size: 0.85rem;
}
</style>
