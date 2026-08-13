<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import * as providersApi from '@/features/providers/providers.api'
import type { DrawerConfigRef, ProviderDefinitionDto } from '@/features/providers/providers.api'

const props = defineProps<{
  open: boolean
  mode: 'create' | 'edit' | 'replaceKey'
  config: DrawerConfigRef | null
  definitions: ProviderDefinitionDto[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved'): void
}>()

const name = ref('')
const providerType = ref('')
const baseUrl = ref('')
const apiKey = ref('')
const saving = ref(false)
const error = ref<string | null>(null)

// Probe (测试连接) state — only for unsaved create configs.
const probing = ref(false)
const probeMessage = ref<string | null>(null)
const probeOk = ref(false)

const selectedDef = computed(() =>
  props.definitions.find((d) => d.provider_type === providerType.value),
)
// Fields are driven by the backend Registry definition — never hard-coded
// per-provider in Vue.
const showBaseUrlInput = computed(() => selectedDef.value?.requires_base_url === true)
const isManaged = computed(() => selectedDef.value?.base_url_mode === 'managed')

function reset(): void {
  name.value = props.config?.name ?? ''
  providerType.value = props.config?.provider_type ?? props.definitions[0]?.provider_type ?? ''
  baseUrl.value = props.config?.base_url ?? ''
  apiKey.value = ''
  error.value = null
  probing.value = false
  probeMessage.value = null
  probeOk.value = false
}

watch(
  () => props.open,
  (open) => {
    if (open) reset()
  },
)

// Any edit to the key/provider/base_url invalidates a previous test result —
// the old success must never linger against new input.
watch([apiKey, providerType, baseUrl], () => {
  probeMessage.value = null
  probeOk.value = false
})

async function onProbe(): Promise<void> {
  if (!providerType.value || probing.value) return
  probing.value = true
  probeMessage.value = null
  probeOk.value = false
  error.value = null
  try {
    const payload: { provider_type: string; api_key?: string; base_url?: string } = {
      provider_type: providerType.value,
    }
    if (apiKey.value) payload.api_key = apiKey.value
    if (baseUrl.value) payload.base_url = baseUrl.value

    const result = await providersApi.probeSearch(payload)
    if (result.status === 'AVAILABLE') {
      const def = props.definitions.find((d) => d.provider_type === result.provider_type)
      const label = def?.display_name ?? result.provider_type
      probeOk.value = true
      probeMessage.value = `连接成功 · ${label} · ${result.latency_ms ?? '—'} ms`
    } else {
      // Failed attempt, or a validation stop (missing Base URL / API key).
      // Backend returns only a safe, stable message — never the key or the raw
      // third-party response.
      probeMessage.value = result.message ?? '连接失败'
    }
  } catch {
    probeMessage.value = '无法连接服务商'
  } finally {
    probing.value = false
  }
}

async function onSubmit(): Promise<void> {
  if (props.mode === 'replaceKey') {
    if (!apiKey.value) {
      error.value = '请输入新的 API Key'
      return
    }
  } else if (!name.value || !providerType.value) {
    error.value = '请填写配置名称与 Provider'
    return
  } else if (showBaseUrlInput.value && !baseUrl.value) {
    error.value = '请填写 Base URL'
    return
  }
  saving.value = true
  error.value = null
  try {
    // base_url is only sent for providers whose definition requires it
    // (custom_compatible_search). Managed providers keep it None and the
    // adapter uses the Registry default.
    const resolvedBaseUrl = showBaseUrlInput.value ? baseUrl.value : undefined
    if (props.mode === 'create') {
      await providersApi.createSearchConfig({
        name: name.value,
        provider_type: providerType.value,
        base_url: resolvedBaseUrl,
        api_key: apiKey.value || undefined,
      })
    } else if (props.mode === 'edit' && props.config) {
      await providersApi.updateSearchConfig(props.config.config_id, {
        name: name.value,
        provider_type: providerType.value,
        base_url: resolvedBaseUrl,
      })
    } else if (props.mode === 'replaceKey' && props.config) {
      await providersApi.replaceSearchKey(props.config.config_id, apiKey.value)
    }
    apiKey.value = ''
    emit('saved')
  } catch {
    error.value = '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="open" class="drawer-overlay" @click.self="emit('close')">
    <aside class="drawer" role="dialog" aria-modal="true">
      <header class="drawer__header">
        <h2 v-if="mode === 'create'">新增搜索服务</h2>
        <h2 v-else-if="mode === 'edit'">编辑搜索服务</h2>
        <h2 v-else>更换 API Key</h2>
        <button type="button" class="close" aria-label="关闭" @click="emit('close')">×</button>
      </header>

      <form @submit.prevent="onSubmit">
        <template v-if="mode === 'replaceKey'">
          <label>
            新的 API Key
            <input v-model="apiKey" type="password" autocomplete="new-password" required />
          </label>
        </template>

        <template v-else-if="mode === 'edit'">
          <label>
            配置名称
            <input v-model="name" type="text" required />
          </label>
          <label>
            Provider
            <select v-model="providerType">
              <option
                v-for="defn in definitions"
                :key="defn.provider_type"
                :value="defn.provider_type"
              >
                {{ defn.display_name }}
              </option>
            </select>
          </label>
          <label v-if="showBaseUrlInput">
            Base URL
            <input
              v-model="baseUrl"
              type="text"
              :placeholder="selectedDef?.default_base_url ?? ''"
              required
            />
          </label>
          <details v-if="isManaged" class="advanced">
            <summary>高级设置</summary>
            <p class="resolved">Base URL：{{ selectedDef?.default_base_url ?? '—' }}</p>
          </details>
          <p v-if="config?.credential_configured" class="configured">已配置</p>
        </template>

        <template v-else>
          <label>
            配置名称
            <input v-model="name" type="text" required />
          </label>
          <label>
            Provider
            <select v-model="providerType">
              <option
                v-for="defn in definitions"
                :key="defn.provider_type"
                :value="defn.provider_type"
              >
                {{ defn.display_name }}
              </option>
            </select>
          </label>
          <label>
            API Key（仅写入，不会回显）
            <input v-model="apiKey" type="password" autocomplete="new-password" />
          </label>
          <label v-if="showBaseUrlInput">
            Base URL
            <input
              v-model="baseUrl"
              type="text"
              :placeholder="selectedDef?.default_base_url ?? ''"
              required
            />
          </label>
          <details v-if="isManaged" class="advanced">
            <summary>高级设置</summary>
            <p class="resolved">Base URL：{{ selectedDef?.default_base_url ?? '—' }}</p>
          </details>

          <div class="probe-row">
            <button type="button" class="probe-btn" :disabled="probing" @click="onProbe">
              {{ probing ? '正在测试…' : '测试连接' }}
            </button>
            <p v-if="probeMessage" :class="probeOk ? 'probe-ok' : 'probe-warn'">
              {{ probeMessage }}
            </p>
          </div>
        </template>

        <p v-if="error" class="form-error">{{ error }}</p>
        <footer class="drawer__footer">
          <button type="button" @click="emit('close')">取消</button>
          <button type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
        </footer>
      </form>
    </aside>
  </div>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 40;
}
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  width: min(420px, 100%);
  background: var(--color-bg);
  border-left: 1px solid var(--color-border);
  padding: 1.25rem;
  overflow-y: auto;
}
.drawer__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.drawer__header h2 {
  font-size: 1.05rem;
  margin: 0;
}
.close {
  border: none;
  background: none;
  font-size: 1.25rem;
  cursor: pointer;
}
label {
  display: block;
  margin-bottom: 0.75rem;
  font-size: 0.9rem;
  color: var(--color-text-secondary);
}
input,
select {
  display: block;
  width: 100%;
  margin-top: 0.25rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
}
.configured {
  color: var(--color-success);
  font-size: 0.9rem;
  margin: 0 0 0.75rem;
}
.probe-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
}
.probe-btn {
  flex-shrink: 0;
  background: none;
  color: var(--color-text);
}
.probe-ok {
  color: var(--color-success);
  font-size: 0.8rem;
  margin: 0;
}
.probe-warn {
  color: var(--color-warning, #b26a00);
  font-size: 0.8rem;
  margin: 0;
}
.advanced {
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
}
.advanced summary {
  cursor: pointer;
  color: var(--color-text-secondary);
}
.resolved {
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  margin: 0.5rem 0;
  word-break: break-all;
}
.drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1rem;
}
button {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
button[type='button'] {
  background: none;
  color: var(--color-text);
}
button:disabled {
  opacity: 0.6;
}
.form-error {
  color: var(--color-danger);
  font-size: 0.85rem;
}
</style>
