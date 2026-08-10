<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import ModelConfigDrawer from '@/features/providers/ModelConfigDrawer.vue'
import * as providersApi from '@/features/providers/providers.api'
import type {
  DrawerConfigRef,
  DrawerMode,
  ModelConfigDto,
  ProviderDefinitionDto,
  SearchConfigDto,
} from '@/features/providers/providers.api'
import SearchConfigDrawer from '@/features/providers/SearchConfigDrawer.vue'

type Tab = 'models' | 'searches'
type DrawerState =
  { open: false } | { open: true; mode: DrawerMode; config: DrawerConfigRef | null }

const activeTab = ref<Tab>('models')
const modelConfigs = ref<ModelConfigDto[]>([])
const searchConfigs = ref<SearchConfigDto[]>([])
const modelDefs = ref<ProviderDefinitionDto[]>([])
const searchDefs = ref<ProviderDefinitionDto[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const testingId = ref<string | null>(null)
const drawer = ref<DrawerState>({ open: false })

function providerDisplay(providerType: string, defs: ProviderDefinitionDto[]): string {
  return defs.find((d) => d.provider_type === providerType)?.display_name ?? providerType
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    available: '可用',
    auth_failed: '认证失败',
    model_not_found: '模型不存在',
    rate_limited: '限流',
    network_error: '网络错误',
    failed: '失败',
    disabled: '已禁用',
    untested: '未测试',
  }
  return map[status] ?? status
}

async function loadAll(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [defs, models, searches] = await Promise.all([
      providersApi.fetchDefinitions(),
      providersApi.listModelConfigs(),
      providersApi.listSearchConfigs(),
    ])
    modelDefs.value = defs.models
    searchDefs.value = defs.searches
    modelConfigs.value = models.configs
    searchConfigs.value = searches.configs
  } catch {
    error.value = '加载配置失败'
  } finally {
    loading.value = false
  }
}

async function onTestModel(config: ModelConfigDto): Promise<void> {
  testingId.value = config.config_id
  error.value = null
  try {
    await providersApi.testModelConnection(config.config_id)
    await loadAll()
  } catch {
    error.value = '测试连接失败'
  } finally {
    testingId.value = null
  }
}

async function onSetDefault(config: ModelConfigDto): Promise<void> {
  error.value = null
  try {
    await providersApi.setModelDefault(config.config_id)
    await loadAll()
  } catch {
    error.value = '设置默认失败'
  }
}

async function onDeleteModel(config: ModelConfigDto): Promise<void> {
  if (!window.confirm(`删除模型配置「${config.name}」？`)) return
  error.value = null
  try {
    await providersApi.deleteModelConfig(config.config_id)
    await loadAll()
  } catch {
    error.value = '删除失败'
  }
}

async function onTestSearch(config: SearchConfigDto): Promise<void> {
  testingId.value = config.config_id
  error.value = null
  try {
    await providersApi.testSearchConnection(config.config_id)
    await loadAll()
  } catch {
    error.value = '测试连接失败'
  } finally {
    testingId.value = null
  }
}

async function onDeleteSearch(config: SearchConfigDto): Promise<void> {
  if (!window.confirm(`删除搜索服务「${config.name}」？`)) return
  error.value = null
  try {
    await providersApi.deleteSearchConfig(config.config_id)
    await loadAll()
  } catch {
    error.value = '删除失败'
  }
}

function openCreate(): void {
  drawer.value = { open: true, mode: 'create', config: null }
}
function openEdit(config: DrawerConfigRef): void {
  drawer.value = { open: true, mode: 'edit', config }
}
function openReplaceKey(config: DrawerConfigRef): void {
  drawer.value = { open: true, mode: 'replaceKey', config }
}
function closeDrawer(): void {
  drawer.value = { open: false }
}
function onDrawerSaved(): void {
  closeDrawer()
  void loadAll()
}

const drawerMode = computed<DrawerMode>(() => (drawer.value.open ? drawer.value.mode : 'create'))
const drawerConfig = computed<DrawerConfigRef | null>(() =>
  drawer.value.open ? drawer.value.config : null,
)

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <section class="models">
    <div class="models__header">
      <RouterLink to="/app">← 返回工作台</RouterLink>
      <h1>模型与搜索服务配置</h1>
    </div>

    <nav class="tabs">
      <button
        type="button"
        :class="['tab', { 'tab--active': activeTab === 'models' }]"
        @click="activeTab = 'models'"
      >
        AI 模型
      </button>
      <button
        type="button"
        :class="['tab', { 'tab--active': activeTab === 'searches' }]"
        @click="activeTab = 'searches'"
      >
        搜索服务
      </button>
    </nav>

    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="loading" class="muted">加载中…</p>

    <template v-if="activeTab === 'models'">
      <div class="toolbar">
        <button type="button" @click="openCreate">新增模型</button>
      </div>
      <table v-if="modelConfigs.length" class="config-table">
        <thead>
          <tr>
            <th>配置名称</th>
            <th>Provider</th>
            <th>Model</th>
            <th>连接状态</th>
            <th>API Key</th>
            <th>默认</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="config in modelConfigs" :key="config.config_id">
            <td>{{ config.name }}</td>
            <td>{{ providerDisplay(config.provider_type, modelDefs) }}</td>
            <td>{{ config.model_name }}</td>
            <td>{{ statusLabel(config.connection_status) }}</td>
            <td>{{ config.credential_configured ? '已配置' : '未配置' }}</td>
            <td>{{ config.is_default ? '默认' : '' }}</td>
            <td class="actions">
              <button type="button" @click="openEdit(config)">编辑</button>
              <button type="button" @click="openReplaceKey(config)">更换 Key</button>
              <button
                type="button"
                :disabled="testingId === config.config_id"
                @click="onTestModel(config)"
              >
                {{ testingId === config.config_id ? '测试中…' : '测试连接' }}
              </button>
              <button type="button" @click="onSetDefault(config)">设为默认</button>
              <button type="button" class="danger" @click="onDeleteModel(config)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loading" class="muted">尚未配置 AI 模型。</p>
    </template>

    <template v-else>
      <div class="toolbar">
        <button type="button" @click="openCreate">新增搜索服务</button>
      </div>
      <table v-if="searchConfigs.length" class="config-table">
        <thead>
          <tr>
            <th>配置名称</th>
            <th>Provider</th>
            <th>Base URL</th>
            <th>连接状态</th>
            <th>API Key</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="config in searchConfigs" :key="config.config_id">
            <td>{{ config.name }}</td>
            <td>{{ providerDisplay(config.provider_type, searchDefs) }}</td>
            <td>{{ config.base_url }}</td>
            <td>{{ statusLabel(config.connection_status) }}</td>
            <td>{{ config.credential_configured ? '已配置' : '未配置' }}</td>
            <td class="actions">
              <button type="button" @click="openEdit(config)">编辑</button>
              <button type="button" @click="openReplaceKey(config)">更换 Key</button>
              <button
                type="button"
                :disabled="testingId === config.config_id"
                @click="onTestSearch(config)"
              >
                {{ testingId === config.config_id ? '测试中…' : '测试连接' }}
              </button>
              <button type="button" class="danger" @click="onDeleteSearch(config)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loading" class="muted">尚未配置搜索服务。</p>
    </template>

    <ModelConfigDrawer
      :open="drawer.open"
      :mode="drawerMode"
      :config="drawerConfig"
      :definitions="modelDefs"
      @close="closeDrawer"
      @saved="onDrawerSaved"
    />
    <SearchConfigDrawer
      :open="drawer.open"
      :mode="drawerMode"
      :config="drawerConfig"
      :definitions="searchDefs"
      @close="closeDrawer"
      @saved="onDrawerSaved"
    />
  </section>
</template>

<style scoped>
.models {
  padding: 1.5rem;
  max-width: 900px;
  margin: 0 auto;
}
.models__header {
  display: flex;
  align-items: baseline;
  gap: 1rem;
}
.models__header h1 {
  font-size: 1.3rem;
  margin: 0 0 1rem;
}
.tabs {
  display: flex;
  gap: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 1rem;
}
.tab {
  padding: 0.5rem 1rem;
  border: none;
  background: none;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab--active {
  border-bottom-color: var(--color-text);
  font-weight: 600;
}
.toolbar {
  margin-bottom: 0.75rem;
}
.config-table {
  width: 100%;
  border-collapse: collapse;
}
.config-table th,
.config-table td {
  border: 1px solid var(--color-border);
  padding: 0.5rem;
  text-align: left;
  font-size: 0.9rem;
}
.actions {
  white-space: nowrap;
}
.actions button {
  margin-right: 0.25rem;
  padding: 0.2rem 0.5rem;
  font-size: 0.8rem;
  cursor: pointer;
}
.danger {
  color: var(--color-danger);
}
.muted {
  color: var(--color-text-secondary);
}
.form-error {
  color: var(--color-danger);
  font-size: 0.85rem;
}
</style>
