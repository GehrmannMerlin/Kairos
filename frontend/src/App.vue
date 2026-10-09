<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  api,
  eventSource,
  type AgentEvent,
  type CollectionField,
  type CollectionMode,
  type CollectionProgress,
  type CollectionRecord,
  type CollectionSpec,
  type FieldEvidence,
  type SearchProviderAvailability,
  type SearchRound,
  type SnapshotMetadata,
  type Task,
  type Workspace,
  type WorkspacePermission,
} from './api'

type CollectionFieldDraft = CollectionField

const task = ref<Task | null>(null)
const workspaces = ref<Workspace[]>([])
const selectedWorkspaceId = ref('')
const displayName = ref('')
const rootPath = ref('')
const permission = ref<WorkspacePermission>('READ_ONLY')
const prompt = ref('')
const events = ref<AgentEvent[]>([])
const busy = ref(false)
const errorMessage = ref('')
const eventStream = ref<EventSource | null>(null)
const collectionSpec = ref<CollectionSpec | null>(null)
const collectionGoal = ref('')
const collectionSeedUrls = ref('')
const collectionMode = ref<CollectionMode>('SPECIFIED_SOURCE')
const collectionTargetCount = ref('5')
const collectionScopeDomains = ref('')
const collectionFields = ref<CollectionFieldDraft[]>([
  { name: 'title', type: 'STRING', required: true, description: '' },
])
const collectionProgress = ref<CollectionProgress | null>(null)
const collectionRecords = ref<CollectionRecord[]>([])
const collectionSearchRounds = ref<SearchRound[]>([])
const searchProvider = ref<SearchProviderAvailability | null>(null)
const selectedRecord = ref<CollectionRecord | null>(null)
const selectedEvidence = ref<FieldEvidence[]>([])
const selectedSnapshot = ref<SnapshotMetadata | null>(null)
const evidenceBusy = ref(false)

const selectedWorkspace = computed(() => workspaces.value.find((item) => item.workspace_id === selectedWorkspaceId.value))
const latestRun = computed(() => task.value?.latest_run ?? null)
const runLabel = computed(() => {
  if (!latestRun.value) return 'READY'
  return latestRun.value.status
})

async function bootstrap(): Promise<void> {
  try {
    const [workspaceItems, provider] = await Promise.all([
      api.listWorkspaces(),
      api.getSearchProviderAvailability().catch(() => null),
    ])
    workspaces.value = workspaceItems
    searchProvider.value = provider
    task.value = await api.createTask()
    try {
      collectionSpec.value = await api.getCollectionSpec(task.value.task_id)
    } catch {
      // A new task has no spec yet; keep the setup form visible.
    }
    if (workspaces.value.length > 0) {
      selectedWorkspaceId.value = workspaces.value[0].workspace_id
      await bindSelectedWorkspace()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Unable to open Task'
  }
}

async function refreshCollectionViews(): Promise<void> {
  if (!task.value || !collectionSpec.value) return
  try {
    const [progress, records, rounds] = await Promise.all([
      api.getCollectionProgress(task.value.task_id),
      api.getRecords(task.value.task_id),
      api.getSearchRounds(task.value.task_id),
    ])
    collectionProgress.value = progress
    collectionRecords.value = records
    collectionSearchRounds.value = rounds
    if (selectedRecord.value) {
      selectedRecord.value = records.find((record) => record.record_id === selectedRecord.value?.record_id) ?? null
    }
  } catch {
    // The run may not have created its first TaskRun yet.
  }
}

async function confirmCollectionSpec(): Promise<void> {
  if (!task.value || collectionSpec.value) return
  const seedUrls = collectionSeedUrls.value
    .split(/\r?\n/)
    .map((url) => url.trim())
    .filter(Boolean)
  const scopeDomains = collectionScopeDomains.value
    .split(/\r?\n|,/)
    .map((domain) => domain.trim().toLowerCase())
    .filter(Boolean)
  const targetCount = Number.parseInt(collectionTargetCount.value, 10)
  const seedRequired = collectionMode.value === 'SPECIFIED_SOURCE'
  const hybridScopeMissing = collectionMode.value === 'HYBRID' && !seedUrls.length && !scopeDomains.length
  const targetMissing = collectionMode.value !== 'SPECIFIED_SOURCE' && (!Number.isInteger(targetCount) || targetCount < 1)
  if (!collectionGoal.value.trim() || (seedRequired && !seedUrls.length) || hybridScopeMissing || targetMissing || !collectionFields.value.length) {
    errorMessage.value = seedRequired
      ? 'Goal, at least one seed URL, and one field are required.'
      : 'Goal, target count, one field, and a valid seed or scope are required.'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    collectionSpec.value = await api.confirmCollectionSpec(task.value.task_id, {
      goal: collectionGoal.value.trim(),
      seed_urls: seedUrls,
      mode: collectionMode.value,
      target_count: seedRequired ? null : targetCount,
      scope_domains: scopeDomains,
      fields: collectionFields.value.map((field) => ({
        ...field,
        name: field.name.trim(),
        description: field.description?.trim() || null,
      })),
    })
    await refreshCollectionViews()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Collection spec could not be confirmed.'
  } finally {
    busy.value = false
  }
}

function addCollectionField(): void {
  const index = collectionFields.value.length + 1
  collectionFields.value.push({ name: `field_${index}`, type: 'STRING', required: false, description: '' })
}

function removeCollectionField(index: number): void {
  if (collectionFields.value.length <= 1 || collectionSpec.value) return
  collectionFields.value.splice(index, 1)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

async function selectCollectionRecord(record: CollectionRecord): Promise<void> {
  selectedRecord.value = record
  evidenceBusy.value = true
  try {
    selectedEvidence.value = task.value ? await api.getRecordEvidence(task.value.task_id, record.record_id) : []
    // Best-effort snapshot metadata (capture method / screenshot availability).
    selectedSnapshot.value = null
    if (task.value && record.snapshot_id) {
      try {
        selectedSnapshot.value = await api.getSnapshotMetadata(task.value.task_id, record.snapshot_id)
      } catch {
        selectedSnapshot.value = null
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Evidence could not be loaded.'
    selectedEvidence.value = []
  } finally {
    evidenceBusy.value = false
  }
}

async function createWorkspace(): Promise<void> {
  if (!displayName.value.trim() || !rootPath.value.trim()) {
    errorMessage.value = 'Display name and absolute path are required.'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const workspace = await api.createWorkspace({
      display_name: displayName.value.trim(),
      root_path: rootPath.value.trim(),
      permission_mode: permission.value,
    })
    workspaces.value = [...workspaces.value, workspace]
    selectedWorkspaceId.value = workspace.workspace_id
    await bindSelectedWorkspace()
    displayName.value = ''
    rootPath.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Workspace could not be created.'
  } finally {
    busy.value = false
  }
}

async function bindSelectedWorkspace(): Promise<void> {
  if (!task.value || !selectedWorkspaceId.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    task.value = await api.bindWorkspace(task.value.task_id, selectedWorkspaceId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Workspace could not be bound.'
  } finally {
    busy.value = false
  }
}

async function disconnectWorkspace(): Promise<void> {
  selectedWorkspaceId.value = ''
  if (task.value) {
    // The first slice keeps disconnect local to the Task UI. The API binding endpoint accepts no workspace id
    // as the explicit disconnect operation in the same owner-scoped route.
    try {
      task.value = await api.bindWorkspace(task.value.task_id, '')
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : 'Workspace could not be disconnected.'
    }
  }
}

function attachEvent(name: string): void {
  if (!eventStream.value) return
  eventStream.value.addEventListener(name, (message) => {
    const event = JSON.parse((message as MessageEvent).data) as AgentEvent
    if (!events.value.some((item) => item.event_id === event.event_id)) {
      events.value.push(event)
    }
    if (
      event.event_type.startsWith('collection.') ||
      event.event_type === 'search.completed' ||
      event.event_type === 'sources.discovered' ||
      event.event_type === 'dedup.completed' ||
      event.event_type === 'saturation.updated'
    ) {
      void refreshCollectionViews()
    }
  })
}

function subscribe(taskId: string, runId: string): void {
  eventStream.value?.close()
  eventStream.value = eventSource(taskId, runId)
  for (const name of [
    'run.started',
    'collection.started',
    'search.started',
    'search.completed',
    'sources.discovered',
    'dedup.completed',
    'saturation.updated',
    'tool.started',
    'tool.completed',
    'tool.failed',
    'snapshot.created',
    'extraction.committed',
    'collection.progress',
    'collection.completed',
    'collection.partially_completed',
    'run.completed',
    'run.failed',
  ]) {
    attachEvent(name)
  }
  eventStream.value.onerror = () => {
    if (latestRun.value?.status === 'RUNNING') errorMessage.value = 'Event stream disconnected; the run may still be active.'
  }
}

async function runAgent(): Promise<void> {
  if (!task.value || !prompt.value.trim()) {
    errorMessage.value = 'Enter an instruction before starting the Agent.'
    return
  }
  busy.value = true
  errorMessage.value = ''
  events.value = []
  try {
    const run = await api.startRun(task.value.task_id, prompt.value.trim())
    task.value = { ...task.value, status: 'RUNNING', latest_run: run }
    subscribe(task.value.task_id, run.task_run_id)
    void refreshAfterRun(task.value.task_id, run.task_run_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Agent run could not be started.'
  } finally {
    busy.value = false
  }
}

async function refreshAfterRun(taskId: string, runId: string): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 500))
    try {
      const freshTask = await api.getTask(taskId)
      task.value = freshTask
      await refreshCollectionViews()
      if (freshTask.latest_run?.task_run_id === runId && freshTask.latest_run.status !== 'RUNNING') {
        eventStream.value?.close()
        return
      }
    } catch {
      return
    }
  }
}

onMounted(() => void bootstrap())
onBeforeUnmount(() => eventStream.value?.close())
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <span class="brand-mark" aria-hidden="true">K</span>
        <div>
          <p class="eyebrow">DURABLE WORKSPACE</p>
          <h1>Kairos Agent</h1>
        </div>
      </div>
      <div class="runtime-chip"><span class="pulse" /> Temporal runtime <strong>{{ runLabel }}</strong></div>
    </header>

    <section class="workspace-grid" aria-label="Kairos task workspace">
      <aside class="control-panel">
        <div class="panel-heading">
          <p class="eyebrow">01 / BIND CONTEXT</p>
          <h2>Workspace</h2>
        </div>
        <label class="field-label" for="workspace-select">Authorized directory</label>
        <select id="workspace-select" v-model="selectedWorkspaceId" @change="bindSelectedWorkspace">
          <option value="">No workspace</option>
          <option v-for="item in workspaces" :key="item.workspace_id" :value="item.workspace_id">
            {{ item.display_name }} · {{ item.permission_mode }}
          </option>
        </select>
        <p v-if="selectedWorkspace" class="path-readout">{{ selectedWorkspace.root_path }}</p>
        <p v-else class="helper-copy">Nothing is mounted. The Agent will not see local files.</p>
        <button v-if="selectedWorkspace" class="button quiet" type="button" @click="disconnectWorkspace">Disconnect</button>

        <div class="rule" />
        <p class="eyebrow">REGISTER LOCAL DIRECTORY</p>
        <label class="field-label" for="display-name">Name</label>
        <input id="display-name" v-model="displayName" type="text" placeholder="Research notes" />
        <label class="field-label" for="root-path">Absolute path</label>
        <input id="root-path" v-model="rootPath" type="text" placeholder="C:\\Users\\you\\workspace" />
        <label class="field-label" for="permission">Permission</label>
        <select id="permission" v-model="permission">
          <option value="READ_ONLY">READ_ONLY · inspect</option>
          <option value="READ_WRITE">READ_WRITE · edit + shell</option>
          <option value="LOCAL_FULL_ACCESS">LOCAL_FULL_ACCESS · local only</option>
          <option value="NONE">NONE · disconnect tools</option>
        </select>
        <button class="button secondary" type="button" :disabled="busy" @click="createWorkspace">Add & bind</button>
        <p class="helper-copy">Folder selection is local-development only. Use an absolute backend path; browser directory upload is not equivalent.</p>

        <div class="rule" />
        <div class="panel-heading collection-heading">
          <p class="eyebrow">03 / COLLECTION SPEC</p>
          <h2>Discovery & collection</h2>
        </div>
        <template v-if="!collectionSpec">
          <label class="field-label" for="collection-mode">Mode</label>
          <select id="collection-mode" v-model="collectionMode">
            <option value="SPECIFIED_SOURCE">Specified sources</option>
            <option value="EXPLORATORY">Exploratory discovery</option>
            <option value="HYBRID">Hybrid · seeds + scoped discovery</option>
          </select>
          <label class="field-label" for="collection-goal">Goal</label>
          <textarea id="collection-goal" v-model="collectionGoal" rows="3" placeholder="Collect one title per source…" />
          <template v-if="collectionMode !== 'EXPLORATORY'">
            <label class="field-label" for="collection-seeds">Seed URLs · one per line</label>
            <textarea id="collection-seeds" v-model="collectionSeedUrls" rows="4" placeholder="https://example.com/page" />
          </template>
          <template v-if="collectionMode !== 'SPECIFIED_SOURCE'">
            <label class="field-label" for="collection-target">Target passed records</label>
            <input id="collection-target" v-model="collectionTargetCount" type="number" min="1" max="100" />
            <label class="field-label" for="collection-domains">Scope domains · optional for exploratory, one per line</label>
            <textarea id="collection-domains" v-model="collectionScopeDomains" rows="2" placeholder="example.org" />
            <p class="helper-copy">
              Search provider: {{ searchProvider?.display_name || 'configured on backend' }} ·
              {{ searchProvider?.configured ? 'ready' : 'credentials required before run' }}
            </p>
          </template>
          <div class="field-header">
            <label class="field-label">Fields</label>
            <button class="text-button" type="button" @click="addCollectionField">+ Add field</button>
          </div>
          <div v-for="(field, index) in collectionFields" :key="index" class="collection-field">
            <input v-model="field.name" aria-label="Field name" placeholder="field_name" />
            <select v-model="field.type" aria-label="Field type">
              <option value="STRING">STRING</option>
              <option value="INTEGER">INTEGER</option>
              <option value="NUMBER">NUMBER</option>
              <option value="BOOLEAN">BOOLEAN</option>
              <option value="DATE">DATE</option>
              <option value="URL">URL</option>
            </select>
            <label class="checkbox-label"><input v-model="field.required" type="checkbox" /> required</label>
            <input v-model="field.description" aria-label="Field description" placeholder="Description" />
            <button class="text-button remove-field" type="button" @click="removeCollectionField(index)">Remove</button>
          </div>
          <button class="button secondary" type="button" :disabled="busy" @click="confirmCollectionSpec">Confirm Collection Spec</button>
        </template>
        <div v-else class="spec-lock">
          <span class="status-badge status-completed">v{{ collectionSpec.version }} · LOCKED</span>
          <p>{{ collectionSpec.goal }}</p>
          <p class="helper-copy">
            {{ collectionSpec.mode }} · {{ collectionSpec.seed_urls.length }} seed sources · {{ collectionSpec.fields.length }} fields
            <span v-if="collectionSpec.target_count"> · target {{ collectionSpec.target_count }}</span>
          </p>
          <p v-if="collectionSpec.scope_domains.length" class="helper-copy">Scope: {{ collectionSpec.scope_domains.join(', ') }}</p>
        </div>
      </aside>

      <section class="chat-panel">
        <div class="panel-heading chat-heading">
          <div>
            <p class="eyebrow">02 / AGENT CONVERSATION</p>
            <h2>Task {{ task?.task_id?.replace('task-', '').slice(0, 8) || 'loading' }}</h2>
          </div>
          <span class="status-badge" :class="`status-${runLabel.toLowerCase()}`">{{ runLabel }}</span>
        </div>
        <div class="conversation-surface">
          <div v-if="!events.length && !task?.final_answer" class="empty-state">
            <span class="empty-index">A0</span>
            <div>
              <h3>Ready for an instruction</h3>
              <p>Choose a workspace, then ask Kairos to inspect a URL or work with the authorized directory.</p>
            </div>
          </div>
          <ol v-else class="event-list" aria-live="polite">
            <li v-for="event in events" :key="event.event_id" class="event-row">
              <span class="event-dot" :class="`dot-${event.event_type.replace('.', '-')}`" />
              <div class="event-copy">
                <div class="event-meta"><span>{{ event.event_type }}</span><time>{{ event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString() : '—' }}</time></div>
                <p>{{ event.summary || event.tool_name || 'Agent event' }}</p>
              </div>
            </li>
          </ol>
          <article v-if="task?.final_answer" class="answer-block">
            <p class="eyebrow">FINAL ANSWER</p>
            <p>{{ task.final_answer }}</p>
          </article>
        </div>
        <div class="composer">
          <label class="field-label" for="prompt">Instruction</label>
          <textarea id="prompt" v-model="prompt" rows="4" placeholder="Check this workspace, then explain what you found…" @keydown.ctrl.enter="runAgent" />
          <div class="composer-footer">
            <span class="composer-hint">Ctrl + Enter to run · events are committed via PostgreSQL</span>
            <button class="button primary" type="button" :disabled="busy || !task" @click="runAgent">{{ busy ? 'Starting…' : 'Run Agent' }} <span aria-hidden="true">↗</span></button>
          </div>
        </div>
      </section>
    </section>

    <section v-if="collectionSpec" class="collection-dashboard" aria-label="Collection results">
      <div class="dashboard-heading">
        <div>
          <p class="eyebrow">04 / COLLECTION OUTPUT</p>
          <h2>Data & evidence</h2>
        </div>
        <span v-if="collectionProgress" class="source-count">
          {{ collectionProgress.sources_discovered }} discovered · {{ collectionProgress.processed_sources }} processed
        </span>
      </div>
      <div v-if="collectionProgress" class="metric-grid">
        <div class="metric"><span>Records</span><strong>{{ collectionProgress.total_records }}</strong></div>
        <div class="metric"><span>Passed canonical</span><strong>{{ collectionProgress.passed_canonical_records }}</strong></div>
        <div class="metric"><span>Needs review</span><strong>{{ collectionProgress.needs_review_records }}</strong></div>
        <div class="metric"><span>Rejected</span><strong>{{ collectionProgress.rejected_records }}</strong></div>
        <div class="metric"><span>To target</span><strong>{{ collectionProgress.remaining_to_target ?? '—' }}</strong></div>
        <div class="metric"><span>Search rounds</span><strong>{{ collectionProgress.search_rounds_completed }} / {{ collectionProgress.max_search_rounds || '—' }}</strong></div>
        <div class="metric"><span>Saturation</span><strong>{{ collectionProgress.saturation_state }}</strong></div>
      </div>
      <div v-if="collectionSpec.mode !== 'SPECIFIED_SOURCE'" class="rounds-strip">
        <div class="panel-heading">
          <p class="eyebrow">SEARCH FRONTIER</p>
          <h3>{{ collectionProgress?.saturation_state === 'SATURATED' ? 'Search saturated' : 'Discovery rounds' }}</h3>
        </div>
        <p v-if="!collectionSearchRounds.length" class="helper-copy">No search rounds committed yet.</p>
        <ol v-else class="round-list">
          <li v-for="round in collectionSearchRounds" :key="round.search_round_id">
            <span>#{{ round.round_number }}</span>
            <strong>{{ round.query }}</strong>
            <small>{{ round.new_sources }} new · {{ round.new_passed_records }} new passed · {{ round.status }}</small>
          </li>
        </ol>
      </div>
      <div class="results-grid">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th v-for="field in collectionSpec.fields" :key="field.name">{{ field.name }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="record in collectionRecords" :key="record.record_id" :class="{ selected: selectedRecord?.record_id === record.record_id }" @click="selectCollectionRecord(record)">
                <td><span class="record-status" :class="`record-${record.status.toLowerCase()}`">{{ record.status }}</span></td>
                <td v-for="field in collectionSpec.fields" :key="field.name">{{ formatValue(record.fields[field.name]) }}</td>
              </tr>
              <tr v-if="!collectionRecords.length"><td class="empty-cell" :colspan="collectionSpec.fields.length + 1">No records committed yet.</td></tr>
            </tbody>
          </table>
        </div>
        <aside class="evidence-panel">
          <div class="panel-heading">
            <p class="eyebrow">FIELD EVIDENCE</p>
            <h3>{{ selectedRecord ? `Record ${selectedRecord.ordinal + 1}` : 'Select a record' }}</h3>
          </div>
          <p v-if="selectedSnapshot" class="snapshot-meta">
            <span class="capture-badge" :class="`capture-${selectedSnapshot.capture_method.toLowerCase()}`">
              Capture: {{ selectedSnapshot.capture_method }}
            </span>
            <span v-if="selectedSnapshot.capture_method === 'BROWSER'">
              <a
                v-if="selectedSnapshot.has_screenshot"
                :href="task ? api.snapshotScreenshotUrl(task.task_id, selectedSnapshot.snapshot_id) : '#'"
                target="_blank"
                rel="noreferrer"
              >Screenshot: View</a>
              <span v-else>Screenshot: none</span>
            </span>
          </p>
          <p v-if="evidenceBusy" class="helper-copy">Loading evidence…</p>
          <p v-else-if="!selectedRecord" class="helper-copy">Click a row to inspect its provenance.</p>
          <div v-else-if="!selectedEvidence.length" class="helper-copy">No evidence was committed for this record.</div>
          <dl v-else class="evidence-list">
            <template v-for="item in selectedEvidence" :key="item.evidence_id">
              <dt>{{ item.field_name }} · {{ item.verified ? 'verified' : 'unverified' }}</dt>
              <dd>
                <span class="evidence-value">{{ formatValue(item.value) }}</span>
                <q>{{ item.quote }}</q>
                <a :href="item.source_url" target="_blank" rel="noreferrer">{{ item.source_url }}</a>
                <small>confidence {{ item.confidence === null ? '—' : item.confidence }}</small>
              </dd>
            </template>
          </dl>
        </aside>
      </div>
    </section>

    <p v-if="errorMessage" class="error-banner" role="alert">{{ errorMessage }}</p>
    <footer class="footer-line"><span>kairos-agent-v1</span><span>web · workspace · durable activity</span><span>local development</span></footer>
  </main>
</template>
