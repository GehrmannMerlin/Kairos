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
const modelName = ref('')
const baseUrl = ref('')
const apiKey = ref('')
const setDefault = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

// Probe (检测连接) state — only for unsaved create configs.
const probing = ref(false)
const probeMessage = ref<string | null>(null)
const probeOk = ref(false)
const providerSelected = ref(false)
const resolvedBaseUrl = ref<string | null>(null)

const selectedDef = computed(() =>
  props.definitions.find((d) => d.provider_type === providerType.value),
)
const showApiKey = computed(() => selectedDef.value?.requires_api_key !== false)
const showBaseUrlInput = computed(
  () =>
    selectedDef.value?.base_url_mode === 'required' ||
    selectedDef.value?.base_url_mode === 'local_required',
)
const isManaged = computed(() => selectedDef.value?.base_url_mode === 'managed')
const resolvedBaseUrlDisplay = computed(
  () => resolvedBaseUrl.value ?? selectedDef.value?.default_base_url ?? null,
)

function reset(): void {
  name.value = props.config?.name ?? ''
  providerType.value = props.config?.provider_type ?? props.definitions[0]?.provider_type ?? ''
  modelName.value = props.config?.model_name ?? ''
  baseUrl.value = props.config?.base_url ?? ''
  apiKey.value = ''
  setDefault.value = props.config?.is_default ?? false
  error.value = null
  probing.value = false
  probeMessage.value = null
  probeOk.value = false
  providerSelected.value = false
  resolvedBaseUrl.value = null
}

watch(
  () => props.open,
  (open) => {
    if (open) reset()
  },
)

function onProviderChange(): void {
  providerSelected.value = true
  resolvedBaseUrl.value = null
  probeMessage.value = null
  probeOk.value = false
}

async function onProbe(): Promise<void> {
  probing.value = true
  probeMessage.value = null
  probeOk.value = false
  error.value = null
  try {
    const payload: {
      api_key?: string
      provider_type?: string
      base_url?: string
      model_name?: string
    } = {}
    if (apiKey.value) payload.api_key = apiKey.value
    if (providerSelected.value) payload.provider_type = providerType.value
    if (baseUrl.value) payload.base_url = baseUrl.value
    if (modelName.value) payload.model_name = modelName.value

    const result = await providersApi.probeModel(payload)
    if (result.status === 'AVAILABLE') {
      const def = props.definitions.find((d) => d.provider_type === result.detected_provider)
      const label = def?.display_name ?? result.detected_provider ?? 'Provider'
      if (result.detected_provider) providerType.value = result.detected_provider
      resolvedBaseUrl.value = result.resolved_base_url
      probeOk.value = true
      probeMessage.value = `连接成功 · ${label} · ${result.latency_ms ?? '—'} ms`
    } else {
      // Failed attempt, or no attempt (AMBIGUOUS / NONE / validation). Backend
      // returns only a safe, stable message — never a raw response or the key.
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
  } else if (
    !name.value ||
    !providerType.value ||
    (selectedDef.value?.requires_model_name && !modelName.value)
  ) {
    error.value = '请填写配置名称、Provider 与模型名称'
    return
  } else if (showBaseUrlInput.value && !baseUrl.value) {
    error.value = '请填写 Base URL'
    return
  }
  saving.value = true
  error.value = null
  try {
    if (props.mode === 'create') {
      await providersApi.createModelConfig({
        name: name.value,
        provider_type: providerType.value,
        model_name: modelName.value,
        base_url: selectedDef.value?.requires_base_url ? baseUrl.value : undefined,
        api_key: apiKey.value || undefined,
        set_default: setDefault.value,
      })
    } else if (props.mode === 'edit' && props.config) {
      await providersApi.updateModelConfig(props.config.config_id, {
        name: name.value,
        provider_type: providerType.value,
        model_name: modelName.value,
        base_url: selectedDef.value?.requires_base_url ? baseUrl.value : undefined,
      })
    } else if (props.mode === 'replaceKey' && props.config) {
      await providersApi.replaceModelKey(props.config.config_id, apiKey.value)
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
        <h2 v-if="mode === 'create'">新增 AI 模型</h2>
        <h2 v-else-if="mode === 'edit'">编辑 AI 模型</h2>
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
            <select v-model="providerType" @change="onProviderChange">
              <option
                v-for="defn in definitions"
                :key="defn.provider_type"
                :value="defn.provider_type"
              >
                {{ defn.display_name }}
              </option>
            </select>
          </label>
          <label v-if="selectedDef?.requires_model_name">
            模型名称
            <input v-model="modelName" type="text" required />
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
            <p class="resolved">Base URL：{{ resolvedBaseUrlDisplay }}</p>
          </details>
          <p v-if="config?.credential_configured" class="configured">已配置</p>
        </template>

        <template v-else>
          <label>
            配置名称
            <input v-model="name" type="text" required />
          </label>

          <label v-if="showApiKey">
            API Key（仅写入，不会回显）
            <input v-model="apiKey" type="password" autocomplete="new-password" />
          </label>

          <div class="probe-row">
            <button type="button" class="probe-btn" :disabled="probing" @click="onProbe">
              {{ probing ? '正在检测连接…' : '检测连接' }}
            </button>
            <p v-if="probeMessage" :class="probeOk ? 'probe-ok' : 'probe-warn'">
              {{ probeMessage }}
            </p>
          </div>

          <label>
            Provider
            <select v-model="providerType" @change="onProviderChange">
              <option
                v-for="defn in definitions"
                :key="defn.provider_type"
                :value="defn.provider_type"
              >
                {{ defn.display_name }}
              </option>
            </select>
          </label>

          <label v-if="selectedDef?.requires_model_name">
            模型名称
            <input v-model="modelName" type="text" required />
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
            <p class="resolved">Base URL：{{ resolvedBaseUrlDisplay }}</p>
          </details>

          <label class="inline-label">
            <input v-model="setDefault" type="checkbox" />
            设为默认（只影响未来任务）
          </label>
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
.inline-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.inline-label input {
  width: auto;
  margin: 0;
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
