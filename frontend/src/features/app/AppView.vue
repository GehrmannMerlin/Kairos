<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { mapApiError } from '@/app/error/apiErrorMapper'
import { createTaskDraft } from '@/features/tasks/chat.api'
import { listTasks, type TaskShellDto } from '@/features/tasks/tasks.api'

// 工作台（D-045/D-034）。新任务 = 自然语言输入 + 快捷入口；快捷入口只是形成
// Task Draft 的辅助方式，不是三套独立 Workflow。
const router = useRouter()
const recent = ref<TaskShellDto[]>([])
const loaded = ref(false)
const content = ref('')
const urlInput = ref('')
const seedUrls = ref<string[]>([])
const creating = ref(false)
const errorMsg = ref<string | null>(null)

const QUICK_STARTS = ['搜集某类信息', '抓取指定网站', '搜索并批量抓取', '使用模板']

const QUICK_EXAMPLES: Record<string, string> = {
  搜集某类信息: '帮我搜集深圳的工业自动化设备供应商，获取公司名、官网、主营产品和联系方式',
  抓取指定网站: '从 https://example.com/suppliers 提取供应商名称、产品和联系电话',
  搜索并批量抓取: '先找深圳工业机器人厂商官方网站，再从这些官网采集产品与联系方式',
}

function useQuickStart(label: string): void {
  if (label === '使用模板') {
    void router.push('/templates')
    return
  }
  content.value = QUICK_EXAMPLES[label] ?? content.value
}

function addUrl(): void {
  const url = urlInput.value.trim()
  if (!url) return
  if (!seedUrls.value.includes(url)) seedUrls.value.push(url)
  urlInput.value = ''
}

function removeUrl(url: string): void {
  seedUrls.value = seedUrls.value.filter((u) => u !== url)
}

async function loadRecent(): Promise<void> {
  try {
    recent.value = (await listTasks()).tasks.slice(0, 5)
  } catch {
    // 列表接口暂不可用时保持 Empty State
  } finally {
    loaded.value = true
  }
}

async function startTask(): Promise<void> {
  const text = content.value.trim()
  if (!text && seedUrls.value.length === 0) {
    errorMsg.value = '请描述采集需求，或添加至少一个网址'
    return
  }
  creating.value = true
  errorMsg.value = null
  try {
    const created = await createTaskDraft({
      content: text || undefined,
      seed_urls: seedUrls.value.length ? seedUrls.value : undefined,
      idempotency_key: crypto.randomUUID(),
    })
    void router.push(`/tasks/${created.task_id}/chat`)
  } catch (err) {
    errorMsg.value = mapApiError(err).message
  } finally {
    creating.value = false
  }
}

onMounted(() => void loadRecent())
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>工作台</h1>
    </header>

    <div class="card workbench__new-task">
      <textarea
        v-model="content"
        class="workbench__input"
        rows="3"
        placeholder="描述你的采集需求，例如：帮我搜集深圳的工业自动化设备供应商，获取公司名、官网、主营产品和联系方式"
      />
      <div class="workbench__quick">
        <button
          v-for="label in QUICK_STARTS"
          :key="label"
          type="button"
          class="chip"
          @click="useQuickStart(label)"
        >
          {{ label }}
        </button>
      </div>
      <div class="workbench__urlrow">
        <input
          v-model="urlInput"
          class="workbench__url"
          type="text"
          placeholder="添加网址（只作为采集方案输入，不会立即抓取）"
          @keydown.enter.prevent="addUrl"
        />
        <button type="button" class="ghost" @click="addUrl">添加网址</button>
      </div>
      <ul v-if="seedUrls.length" class="workbench__urls">
        <li v-for="u in seedUrls" :key="u">
          <span>{{ u }}</span>
          <button type="button" class="remove" aria-label="移除" @click="removeUrl(u)">×</button>
        </li>
      </ul>
      <p v-if="errorMsg" class="workbench__error">{{ errorMsg }}</p>
      <div class="workbench__actions">
        <button type="button" class="primary" :disabled="creating" @click="startTask">
          {{ creating ? '创建中…' : '开始采集' }}
        </button>
      </div>
    </div>

    <div class="workbench__section">
      <h2 class="workbench__section-title">最近任务</h2>
      <ul v-if="recent.length" class="workbench__recent">
        <li v-for="t in recent" :key="t.task_id">
          <RouterLink :to="`/tasks/${t.task_id}/chat`" class="task-link">{{ t.title }}</RouterLink>
          <span class="muted">{{ t.state }}</span>
        </li>
      </ul>
      <p v-else class="empty">暂无任务</p>
    </div>
  </section>
</template>

<style scoped>
.workbench__input {
  display: block;
  width: 100%;
  padding: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  resize: vertical;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}
.workbench__quick {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
  flex-wrap: wrap;
}
.chip {
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 0.8rem;
}
.workbench__urlrow {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.workbench__url {
  flex: 1;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.85rem;
}
.workbench__urls {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
}
.workbench__urls li {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.2rem 0;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.workbench__error {
  color: #c62828;
  font-size: 0.85rem;
}
.workbench__actions {
  margin-top: 0.75rem;
  display: flex;
  justify-content: flex-end;
}
button.ghost {
  padding: 0.4rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  font-size: 0.85rem;
}
button.primary {
  padding: 0.5rem 1.2rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
button.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.remove {
  border: none;
  background: none;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.workbench__section-title {
  font-size: 1rem;
  margin: 0 0 0.75rem;
}
.workbench__recent {
  list-style: none;
  margin: 0;
  padding: 0;
}
.workbench__recent li {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px dashed var(--color-border);
}
.task-link {
  color: var(--color-text);
  text-decoration: none;
}
.task-link:hover {
  text-decoration: underline;
}
</style>
