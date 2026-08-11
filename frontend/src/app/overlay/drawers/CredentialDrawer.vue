<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { openDrawer } from '@/app/overlay/drawer.store'
import { mapApiError } from '@/app/error/apiErrorMapper'
import {
  storeTaskCredential,
  type StoreCredentialCommand,
  type WebsiteCredentialType,
} from '@/features/tasks/credentials.api'

// Credential Drawer 真实业务（D-059 / D-067）。保存走 CredentialVault（加密），
// Task 只保留 credential_id；创建 credential_access Approval 后打开审批 Drawer。
// 本组件不读取/回显任何明文之外的内容。
const props = defineProps<{ payload?: unknown }>()
const payload = (props.payload ?? {}) as { taskId?: string | number; domain?: string }

const type = ref<WebsiteCredentialType>('cookie')
const scope = ref<'CURRENT_TASK' | 'SAVED_DOMAIN'>('CURRENT_TASK')
const domain = ref(payload.domain ?? '')
const cookies = reactive<{ name: string; value: string; domain: string; path: string }[]>([
  { name: '', value: '', domain: '', path: '/' },
])
const username = ref('')
const password = ref('')

const busy = ref(false)
const notice = ref<{ kind: 'ok' | 'error'; text: string } | null>(null)

const canSubmit = computed(() => {
  if (!payload.taskId || !domain.value.trim()) return false
  if (type.value === 'cookie') {
    return cookies.some((c) => c.name.trim() && c.value.length > 0)
  }
  return username.value.trim().length > 0 && password.value.length > 0
})

function addCookieRow(): void {
  cookies.push({ name: '', value: '', domain: '', path: '/' })
}

function removeCookieRow(index: number): void {
  if (cookies.length > 1) cookies.splice(index, 1)
}

async function onSubmit(): Promise<void> {
  if (!payload.taskId || busy.value) return
  busy.value = true
  notice.value = null
  try {
    const command: StoreCredentialCommand = {
      type: type.value,
      payload:
        type.value === 'cookie'
          ? {
              cookies: cookies
                .filter((c) => c.name.trim() && c.value.length > 0)
                .map((c) => ({
                  name: c.name.trim(),
                  value: c.value,
                  domain: c.domain.trim() || domain.value.trim(),
                  path: c.path || '/',
                })),
            }
          : { username: username.value.trim(), password: password.value },
      scope: scope.value,
      domain: domain.value.trim(),
    }
    const result = await storeTaskCredential(payload.taskId, command)
    if (result.approval_id) {
      // 真正使用非公开凭据前必须审批：打开 Approval Drawer（D-017 / D-057）
      openDrawer('APPROVAL', { approvalId: result.approval_id })
      notice.value = { kind: 'ok', text: '凭据已保存，等待审批' }
    } else {
      notice.value = { kind: 'ok', text: '凭据已保存' }
    }
  } catch (err) {
    notice.value = { kind: 'error', text: mapApiError(err).message }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="credential-form" @submit.prevent="onSubmit">
    <div class="field">
      <label>类型</label>
      <select v-model="type" data-test="type">
        <option value="cookie">Cookie</option>
        <option value="username_password">用户名 / 密码</option>
      </select>
    </div>

    <div class="field">
      <label>使用范围</label>
      <select v-model="scope" data-test="scope">
        <option value="CURRENT_TASK">仅当前任务</option>
        <option value="SAVED_DOMAIN">保存供该域名后续使用</option>
      </select>
    </div>

    <div class="field">
      <label>域名</label>
      <input v-model="domain" data-test="domain" placeholder="example.com" />
    </div>

    <template v-if="type === 'cookie'">
      <div v-for="(c, i) in cookies" :key="i" class="cookie-row">
        <input v-model="c.name" data-test="cookie-name" placeholder="name" />
        <input v-model="c.value" data-test="cookie-value" placeholder="value" />
        <button type="button" class="ghost" :disabled="cookies.length <= 1" @click="removeCookieRow(i)">
          删除
        </button>
      </div>
      <button type="button" class="ghost" @click="addCookieRow">+ 添加 Cookie</button>
    </template>

    <template v-else>
      <div class="field">
        <label>用户名</label>
        <input v-model="username" data-test="username" autocomplete="username" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="password" data-test="password" type="password" autocomplete="current-password" />
      </div>
    </template>

    <div class="actions">
      <button type="submit" class="primary" :disabled="!canSubmit || busy" data-test="submit">
        保存并申请使用
      </button>
    </div>
    <p v-if="notice" class="notice" :class="`notice--${notice.kind}`">{{ notice.text }}</p>
  </form>
</template>

<style scoped>
.credential-form {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.field label {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
input,
select {
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font: inherit;
  font-size: 0.85rem;
  background: var(--color-bg);
  color: var(--color-text);
}
.cookie-row {
  display: flex;
  gap: 0.4rem;
}
.cookie-row input {
  flex: 1;
}
button.primary,
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
button.ghost {
  background: transparent;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.notice {
  font-size: 0.85rem;
  margin: 0;
}
.notice--ok {
  color: var(--color-success, #2e7d32);
}
.notice--error {
  color: var(--color-danger, #c0392b);
}
</style>
