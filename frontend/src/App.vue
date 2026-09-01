<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api, eventSource, type AgentEvent, type Task, type Workspace, type WorkspacePermission } from './api'

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

const selectedWorkspace = computed(() => workspaces.value.find((item) => item.workspace_id === selectedWorkspaceId.value))
const latestRun = computed(() => task.value?.latest_run ?? null)
const runLabel = computed(() => {
  if (!latestRun.value) return 'READY'
  return latestRun.value.status
})

async function bootstrap(): Promise<void> {
  try {
    workspaces.value = await api.listWorkspaces()
    task.value = await api.createTask()
    if (workspaces.value.length > 0) {
      selectedWorkspaceId.value = workspaces.value[0].workspace_id
      await bindSelectedWorkspace()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Unable to open Task'
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
  })
}

function subscribe(taskId: string, runId: string): void {
  eventStream.value?.close()
  eventStream.value = eventSource(taskId, runId)
  for (const name of ['run.started', 'tool.started', 'tool.completed', 'tool.failed', 'run.completed', 'run.failed']) {
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

    <p v-if="errorMessage" class="error-banner" role="alert">{{ errorMessage }}</p>
    <footer class="footer-line"><span>kairos-agent-v1</span><span>web · workspace · durable activity</span><span>local development</span></footer>
  </main>
</template>

