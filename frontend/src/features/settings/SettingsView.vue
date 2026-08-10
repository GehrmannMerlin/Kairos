<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { mapApiError } from '@/app/error/apiErrorMapper'
import { authStore } from '@/features/auth/useAuth'
import * as authApi from '@/features/auth/auth.api'
import type { SessionDto } from '@/features/auth/auth.api'
import * as providersApi from '@/features/providers/providers.api'

// 设置四区（D-052）。账户资料/安全/采集默认值接入 M-02/M-03 真实能力；
// 尚未实现的字段扩展/高级运行默认值/存储与数据为明确「后续接入」，禁止假开关。
const router = useRouter()

// ① 账户资料
const email = computed(() => authStore.user.value?.email ?? '')
const displayName = computed(() => authStore.user.value?.display_name ?? null)

// ② 安全
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const changingPassword = ref(false)
const passwordMsg = ref<{ kind: 'ok' | 'error'; text: string } | null>(null)
const sessions = ref<SessionDto[]>([])
const sessionsLoading = ref(false)

// ③ 采集默认值（真实默认模型）
const defaultModelName = ref<string | null>(null)
const defaultsLoading = ref(false)

const error = ref<string | null>(null)

async function onChangePassword(): Promise<void> {
  if (!currentPassword.value || !newPassword.value || !confirmPassword.value) {
    passwordMsg.value = { kind: 'error', text: '请填写完整' }
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    passwordMsg.value = { kind: 'error', text: '两次输入的新密码不一致' }
    return
  }
  changingPassword.value = true
  passwordMsg.value = null
  try {
    await authApi.changePassword(currentPassword.value, newPassword.value, confirmPassword.value)
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    passwordMsg.value = { kind: 'ok', text: '密码已更新' }
  } catch (err) {
    passwordMsg.value = { kind: 'error', text: mapApiError(err).message }
  } finally {
    changingPassword.value = false
  }
}

async function loadSessions(): Promise<void> {
  sessionsLoading.value = true
  error.value = null
  try {
    sessions.value = (await authApi.listSessions()).sessions
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    sessionsLoading.value = false
  }
}

async function onLogoutOthers(): Promise<void> {
  error.value = null
  try {
    await authApi.logoutOthers()
    await loadSessions()
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

async function onRevokeSession(sessionId: number): Promise<void> {
  error.value = null
  try {
    await authApi.revokeSession(sessionId)
    await loadSessions()
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

async function onLogout(): Promise<void> {
  await authStore.logout()
  await router.push('/login')
}

async function loadDefaultModel(): Promise<void> {
  defaultsLoading.value = true
  try {
    const models = await providersApi.listModelConfigs()
    defaultModelName.value = models.configs.find((c) => c.is_default)?.name ?? null
  } catch {
    // 模型列表接口暂不可用时保持真实空态
  } finally {
    defaultsLoading.value = false
  }
}

onMounted(() => {
  void loadSessions()
  void loadDefaultModel()
})
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>设置</h1>
    </header>
    <p v-if="error" class="form-error">{{ error }}</p>

    <section class="settings__section">
      <h2>账户资料</h2>
      <dl class="settings__account">
        <div><dt>邮箱</dt><dd>{{ email }}</dd></div>
        <div><dt>显示名称</dt><dd>{{ displayName ?? '—' }}</dd></div>
      </dl>
    </section>

    <section class="settings__section">
      <h2>安全</h2>

      <div class="settings__block">
        <h3>修改密码</h3>
        <form class="settings__form" @submit.prevent="onChangePassword">
          <label>
            当前密码
            <input v-model="currentPassword" type="password" autocomplete="current-password" />
          </label>
          <label>
            新密码
            <input v-model="newPassword" type="password" autocomplete="new-password" />
          </label>
          <label>
            确认新密码
            <input v-model="confirmPassword" type="password" autocomplete="new-password" />
          </label>
          <button type="submit" :disabled="changingPassword">
            {{ changingPassword ? '提交中…' : '更新密码' }}
          </button>
        </form>
        <p v-if="passwordMsg" :class="passwordMsg.kind === 'ok' ? 'ok' : 'form-error'">
          {{ passwordMsg.text }}
        </p>
      </div>

      <div class="settings__block">
        <h3>当前会话</h3>
        <p v-if="sessionsLoading" class="muted">加载中…</p>
        <ul v-else-if="sessions.length" class="settings__sessions">
          <li v-for="s in sessions" :key="s.id">
            <span>会话 #{{ s.id }} · {{ s.created_at }}</span>
            <span v-if="s.is_current" class="ok">当前</span>
            <button v-else type="button" @click="onRevokeSession(s.id)">退出该设备</button>
          </li>
        </ul>
        <p v-else class="muted">暂无会话</p>
        <button type="button" @click="onLogoutOthers">退出其他设备</button>
      </div>

      <div class="settings__block">
        <h3>退出登录</h3>
        <button type="button" @click="onLogout">退出登录</button>
      </div>

      <div class="settings__block">
        <h3>已保存网站凭据</h3>
        <p class="muted">网站登录凭据管理将在后续模块接入</p>
      </div>
    </section>

    <section class="settings__section">
      <h2>采集默认值</h2>
      <div class="settings__block">
        <h3>默认模型</h3>
        <p v-if="defaultsLoading" class="muted">加载中…</p>
        <p v-else-if="defaultModelName" class="muted">
          默认模型：{{ defaultModelName }}（
          <RouterLink to="/models">去模型配置</RouterLink>）
        </p>
        <p v-else class="muted">
          未配置默认模型（
          <RouterLink to="/models">去模型配置</RouterLink>）
        </p>
      </div>
      <div class="settings__block">
        <h3>字段扩展默认行为</h3>
        <p class="muted">将在后续模块接入</p>
      </div>
      <div class="settings__block">
        <h3>高级运行默认值</h3>
        <p class="muted">将在后续模块接入</p>
      </div>
    </section>

    <section class="settings__section">
      <h2>存储与数据</h2>
      <p class="muted">任务 / 记录 / 证据 / 导出统计、清理已删除任务文件、删除全部数据等危险操作将在后续模块接入</p>
    </section>
  </section>
</template>

<style scoped>
.settings__account {
  margin: 0;
}
.settings__account div {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.3rem 0;
}
.settings__account dt {
  color: var(--color-text-secondary);
}
.settings__account dd {
  margin: 0;
}
.settings__block {
  margin-bottom: 1rem;
}
.settings__block h3 {
  font-size: 0.95rem;
  margin: 0 0 0.5rem;
}
.settings__form {
  display: grid;
  gap: 0.5rem;
  max-width: 420px;
}
.settings__form label {
  display: flex;
  flex-direction: column;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.settings__form input {
  margin-top: 0.2rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}
.settings__form button,
.settings__block button {
  justify-self: start;
  margin-top: 0.25rem;
  padding: 0.45rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
}
.settings__sessions {
  list-style: none;
  margin: 0 0 0.5rem;
  padding: 0;
}
.settings__sessions li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.3rem 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 0.9rem;
}
.ok {
  color: var(--color-success);
}
</style>
