<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/app/error/ApiError'
import { authStore } from '@/features/auth/useAuth'

const router = useRouter()
const email = ref('')
const password = ref('')
const submitting = ref(false)
const error = ref<string | null>(null)

async function onSubmit(): Promise<void> {
  if (!email.value || !password.value) {
    error.value = '请输入邮箱和密码'
    return
  }
  submitting.value = true
  error.value = null
  try {
    await authStore.login(email.value, password.value)
    await router.push('/app')
  } catch (err) {
    error.value = err instanceof ApiError ? err.detail : '登录失败，请稍后再试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="auth-page">
    <div class="auth-card">
      <h1>登录 Kairos</h1>
      <form @submit.prevent="onSubmit">
        <label>
          邮箱
          <input v-model="email" type="email" autocomplete="email" required />
        </label>
        <label>
          密码
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <p v-if="error" class="form-error">{{ error }}</p>
        <button type="submit" :disabled="submitting">
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>
      <p class="auth-switch">还没有账号？<RouterLink to="/register">注册</RouterLink></p>
    </div>
  </section>
</template>

<style scoped>
.auth-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
}
.auth-card {
  width: 100%;
  max-width: 360px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 1.5rem;
}
.auth-card h1 {
  font-size: 1.2rem;
  margin: 0 0 1rem;
}
label {
  display: block;
  margin-bottom: 0.75rem;
  font-size: 0.9rem;
  color: var(--color-text-secondary);
}
input {
  display: block;
  width: 100%;
  margin-top: 0.25rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}
button {
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.55rem;
  border: none;
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
button:disabled {
  opacity: 0.6;
}
.form-error {
  color: var(--color-danger);
  font-size: 0.85rem;
  margin: 0.5rem 0;
}
.auth-switch {
  margin-top: 1rem;
  font-size: 0.9rem;
}
</style>
