<template>
  <div class="admin-console">
    <header class="admin-header">
      <div>
        <div class="admin-eyebrow">INTRANET GOVERNANCE</div>
        <h1>Admin Control Room</h1>
        <p>Operate users, quotas, workspaces and shared guidance with an audit trail.</p>
      </div>
      <button class="icon-button" title="Close admin console" @click="$emit('close')">×</button>
    </header>

    <div :class="['scope-bar', { elevated: grantActive }]">
      <div class="scope-state">
        <span class="scope-dot"></span>
        <div>
          <strong>{{ grantActive ? 'Temporary content access' : 'Metadata only' }}</strong>
          <span v-if="grantActive">
            User {{ activeGrant.target_user_id }} · expires {{ formatTime(activeGrant.expires_at) }}
          </span>
          <span v-else>User file contents remain sealed until a reasoned, time-bound grant is issued.</span>
        </div>
      </div>
      <code>{{ grantActive ? activeGrant.id.slice(0, 8) : 'SAFE-SCOPE' }}</code>
    </div>

    <div class="admin-body">
      <nav class="admin-nav" aria-label="Administration sections">
        <button
          v-for="item in sections"
          :key="item.id"
          :class="{ active: section === item.id }"
          @click="switchSection(item.id)"
        >
          <span class="nav-mark">{{ item.mark }}</span>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <main class="admin-content">
        <div v-if="error" class="notice error" role="alert">
          <span>{{ error }}</span>
          <button @click="error = ''">Dismiss</button>
        </div>
        <div v-if="notice" class="notice success" role="status">{{ notice }}</div>
        <div v-if="loading" class="loading-line"><span></span></div>

        <section v-if="section === 'overview'" class="panel-stack">
          <div class="section-heading">
            <div><span>OPERATIONS</span><h2>What needs attention</h2></div>
            <button class="secondary-button" @click="loadOverview">Refresh</button>
          </div>

          <div class="metric-strip">
            <article><span>Active users</span><strong>{{ overview.users?.active ?? 0 }}</strong><small>{{ overview.users?.total ?? 0 }} total</small></article>
            <article><span>Completed tasks</span><strong>{{ overview.tasks?.task_count ?? 0 }}</strong><small>{{ overview.tasks?.failed ?? 0 }} failed</small></article>
            <article><span>Tool calls</span><strong>{{ overview.tasks?.tool_calls ?? 0 }}</strong><small>framework counted</small></article>
            <article class="risk"><span>Safety blocks</span><strong>{{ overview.tasks?.safety_interceptions ?? 0 }}</strong><small>{{ overview.tasks?.confirmation_count ?? 0 }} confirmations</small></article>
          </div>

          <div class="data-panel">
            <div class="panel-title"><h3>Recent task runs</h3><span>Redacted task metadata across users</span></div>
            <div v-if="!overview.recent_tasks?.length" class="empty-state">No terminal task traces are available.</div>
            <table v-else>
              <thead><tr><th>User</th><th>Task</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Action</th></tr></thead>
              <tbody>
                <tr v-for="task in overview.recent_tasks" :key="`${task.user_id}-${task.trace_id}`">
                  <td><code>#{{ task.user_id }}</code></td>
                  <td><span class="task-title">{{ task.request_summary || task.trace_id }}</span><code>{{ task.trace_id?.slice(0, 8) }}</code></td>
                  <td><span :class="['status-pill', task.status]">{{ task.status }}</span></td>
                  <td>{{ formatDuration(task.duration_ms) }}</td>
                  <td>{{ formatNumber(task.metrics?.total_tokens || 0) }}</td>
                  <td>
                    <button
                      v-if="!['succeeded', 'failed', 'cancelled'].includes(task.status)"
                      class="table-action danger"
                      @click="cancelTask(task)"
                    >Cancel</button>
                    <span v-else>—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-else-if="section === 'users'" class="panel-stack">
          <div class="section-heading">
            <div><span>IDENTITIES</span><h2>User operations</h2></div>
            <div class="search-box">
              <input v-model="userQuery" placeholder="Search username or email" @keyup.enter="loadUsers" />
              <button @click="loadUsers">Search</button>
            </div>
          </div>

          <div class="users-layout">
            <div class="data-panel user-directory">
              <div class="panel-title"><h3>Directory</h3><span>{{ usersTotal }} users</span></div>
              <button
                v-for="user in users"
                :key="user.id"
                :class="['user-row', { selected: selectedUser?.user?.id === user.id }]"
                @click="selectUser(user.id)"
              >
                <span class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span>
                <span class="user-copy"><strong>{{ user.username }}</strong><small>{{ user.email }}</small></span>
                <span :class="['status-pill', user.is_active ? 'succeeded' : 'failed']">{{ user.is_active ? 'active' : 'disabled' }}</span>
              </button>
              <div v-if="!users.length" class="empty-state">No users match this filter.</div>
            </div>

            <div v-if="selectedUser" class="user-detail panel-stack">
              <div class="data-panel profile-card">
                <div>
                  <span class="detail-kicker">USER {{ selectedUser.user.id }}</span>
                  <h3>{{ selectedUser.user.username }}</h3>
                  <p>{{ selectedUser.user.email }} · {{ selectedUser.user.role }}</p>
                </div>
                <span :class="['status-pill', selectedUser.user.is_active ? 'succeeded' : 'failed']">
                  {{ selectedUser.user.is_active ? 'active' : 'disabled' }}
                </span>
              </div>

              <div class="mini-metrics">
                <article><span>Tasks today</span><strong>{{ selectedUser.usage.daily_tasks }}</strong></article>
                <article><span>Tokens today</span><strong>{{ formatNumber(selectedUser.usage.daily_tokens) }}</strong></article>
                <article><span>Tokens this month</span><strong>{{ formatNumber(selectedUser.usage.monthly_tokens) }}</strong></article>
                <article><span>Workspace</span><strong>{{ formatBytes(selectedUser.workspace.bytes) }}</strong></article>
              </div>

              <div class="data-panel form-panel">
                <div class="panel-title"><h3>Account state</h3><span>Changes take effect from the live database role.</span></div>
                <label>Reason<input v-model="statusReason" placeholder="Incident, offboarding or account recovery" /></label>
                <button class="danger-button" :disabled="statusReason.length < 3" @click="toggleUserStatus">
                  {{ selectedUser.user.is_active ? 'Disable account' : 'Restore account' }}
                </button>
                <button class="secondary-button" :disabled="statusReason.length < 3" @click="revokeSessions">
                  Revoke sessions & API keys
                </button>
              </div>

              <div class="data-panel form-panel">
                <div class="panel-title"><h3>Metered usage quota</h3><span>Concurrency and server safety ceilings remain enforced.</span></div>
                <label class="quota-toggle">
                  <input v-model="quotaDraft.enabled" type="checkbox" />
                  Enforce this user's daily and monthly usage quota
                </label>
                <div class="form-grid">
                  <label>Daily tasks<input v-model.number="quotaDraft.daily_task_limit" type="number" min="1" /></label>
                  <label>Daily tokens<input v-model.number="quotaDraft.daily_token_limit" type="number" min="1000" /></label>
                  <label>Monthly tokens<input v-model.number="quotaDraft.monthly_token_limit" type="number" min="1000" /></label>
                  <label>Concurrent tasks<input v-model.number="quotaDraft.concurrent_task_limit" type="number" min="1" /></label>
                  <label>Workspace bytes<input v-model.number="quotaDraft.workspace_bytes_limit" type="number" min="1048576" /></label>
                  <label>Change reason<input v-model="quotaReason" placeholder="Why this limit is changing" /></label>
                </div>
                <button class="primary-button" :disabled="quotaReason.length < 3" @click="saveQuota">Save quota</button>
              </div>

              <div class="data-panel workspace-panel">
                <div class="panel-title">
                  <div><h3>Workspace metadata</h3><span>File content remains guarded by a temporary grant.</span></div>
                  <button class="secondary-button" @click="loadWorkspace">Refresh tree</button>
                </div>
                <div class="workspace-grid">
                  <div class="workspace-tree">
                    <button
                      v-for="node in workspaceEntries"
                      :key="node.path"
                      :disabled="node.type !== 'file'"
                      :class="{ sensitive: node.sensitive }"
                      @click="chooseWorkspaceFile(node)"
                    >
                      <span>{{ node.type === 'dir' ? '▾' : node.type === 'symlink' ? '↗' : '·' }}</span>
                      <span>{{ node.path || node.name }}</span>
                      <small v-if="node.type === 'file'">{{ formatBytes(node.size) }}</small>
                    </button>
                    <div v-if="!workspaceEntries.length" class="empty-state">Load the workspace tree to inspect metadata.</div>
                  </div>
                  <div class="guarded-preview">
                    <template v-if="selectedWorkspacePath">
                      <div class="preview-heading"><code>{{ selectedWorkspacePath }}</code></div>
                      <template v-if="workspacePreview">
                        <pre>{{ workspacePreview.content || (workspacePreview.binary ? '[Binary file — content not rendered]' : '') }}</pre>
                      </template>
                      <template v-else>
                        <p>Explain the support or incident need before opening this file.</p>
                        <textarea v-model="grantReason" rows="3" placeholder="Ticket or operational reason (minimum 8 characters)"></textarea>
                        <button class="warning-button" :disabled="grantReason.length < 8" @click="grantAndRead">
                          Request 10-minute read access
                        </button>
                      </template>
                    </template>
                    <div v-else class="empty-state">Select a non-sensitive file to request guarded preview.</div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="data-panel empty-detail">Select a user to inspect operational metadata.</div>
          </div>
        </section>

        <section v-else-if="section === 'skills'" class="panel-stack">
          <div class="section-heading">
            <div><span>PROMPT SUPPLY CHAIN</span><h2>Shared Skill registry</h2></div>
            <button class="primary-button" @click="newSkill">New managed Skill</button>
          </div>

          <div class="skills-layout">
            <div class="data-panel skill-list">
              <button
                v-for="skill in skills"
                :key="`${skill.source}-${skill.name}`"
                :class="{ selected: skillEditor.name === skill.name }"
                @click="selectSkill(skill)"
              >
                <span><strong>{{ skill.name }}</strong><small>{{ skill.description || 'No description' }}</small></span>
                <span :class="['status-pill', skill.status === 'published' || skill.status === 'builtin' ? 'succeeded' : 'pending']">{{ skill.status }}</span>
              </button>
            </div>

            <div class="data-panel skill-editor">
              <div class="panel-title">
                <div><h3>{{ skillEditor.name || 'Select a Skill' }}</h3><span>{{ skillEditor.source === 'builtin' ? 'Built-in Skills are immutable.' : 'Managed versions are persisted and auditable.' }}</span></div>
                <code v-if="skillEditor.active_version">v{{ skillEditor.active_version }}</code>
              </div>
              <template v-if="skillEditor.name">
                <div class="form-grid two">
                  <label>Name<input v-model="skillEditor.name" :disabled="Boolean(skillEditor.persisted)" placeholder="python-quality" /></label>
                  <label>Description<input v-model="skillEditor.description" :disabled="skillEditor.source === 'builtin'" /></label>
                </div>
                <label class="editor-label">SKILL.md
                  <textarea v-model="skillEditor.content" :disabled="skillEditor.source === 'builtin'" rows="18" spellcheck="false"></textarea>
                </label>
                <div v-if="skillValidation" :class="['validation-box', { invalid: !skillValidation.valid }]">
                  <strong>{{ skillValidation.valid ? 'Validation passed' : 'Validation failed' }}</strong>
                  <span>{{ skillValidation.bytes }} bytes · ~{{ skillValidation.estimated_tokens }} tokens</span>
                  <ul v-if="skillValidation.errors?.length"><li v-for="item in skillValidation.errors" :key="item">{{ item }}</li></ul>
                  <ul v-if="skillValidation.warnings?.length"><li v-for="item in skillValidation.warnings" :key="item">{{ item }}</li></ul>
                </div>
                <template v-if="skillEditor.source !== 'builtin'">
                  <div class="skill-actions">
                    <button class="secondary-button" @click="saveSkill">Save draft</button>
                    <button class="secondary-button" :disabled="!skillEditor.persisted" @click="validateSkill">Validate</button>
                    <input v-model="skillChangelog" placeholder="Version changelog" />
                    <button class="primary-button" :disabled="!skillEditor.persisted || skillChangelog.length < 3" @click="publishSkill">Publish version</button>
                    <button v-if="skillEditor.status === 'published'" class="danger-button" @click="retireSkill">Retire</button>
                  </div>
                  <div v-if="skillEditor.versions?.length" class="version-list">
                    <span v-for="version in skillEditor.versions" :key="version.version">
                      <span><code>v{{ version.version }}</code> {{ version.changelog }}</span>
                      <button
                        v-if="version.version !== skillEditor.active_version"
                        :disabled="skillChangelog.length < 3"
                        @click="rollbackSkill(version.version)"
                      >Roll back</button>
                    </span>
                  </div>
                </template>
              </template>
              <div v-else class="empty-state">Select an existing Skill or create a managed draft.</div>
            </div>
          </div>
        </section>

        <section v-else-if="section === 'audit'" class="panel-stack">
          <div class="section-heading"><div><span>EVIDENCE</span><h2>Privileged action ledger</h2></div><button class="secondary-button" @click="loadAudit">Refresh</button></div>
          <div class="data-panel">
            <div v-if="!auditLogs.length" class="empty-state">No privileged actions have been recorded yet.</div>
            <table v-else>
              <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Reason</th><th>Outcome</th></tr></thead>
              <tbody>
                <tr v-for="row in auditLogs" :key="row.id">
                  <td>{{ formatDate(row.created_at) }}</td><td><code>#{{ row.actor_user_id }}</code></td><td><code>{{ row.action }}</code></td>
                  <td>{{ row.target_type }} / {{ row.target_id }}</td><td>{{ row.reason || '—' }}</td><td><span :class="['status-pill', row.outcome === 'succeeded' ? 'succeeded' : 'failed']">{{ row.outcome }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-else-if="section === 'system'" class="panel-stack">
          <div class="section-heading"><div><span>RUNTIME</span><h2>System health</h2></div><button class="secondary-button" @click="loadSystem">Refresh</button></div>
          <div v-if="systemHealth" class="system-grid">
            <article v-for="(value, key) in systemHealth.checks" :key="key" class="data-panel health-card">
              <span>{{ key }}</span><strong :class="value">{{ value }}</strong>
            </article>
            <article class="data-panel storage-card">
              <span>Workspace volume</span><strong>{{ formatBytes(systemHealth.storage.used) }} used</strong><small>{{ formatBytes(systemHealth.storage.free) }} free</small>
            </article>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import * as api from '../../api/client.js'

defineEmits(['close'])

const sections = [
  { id: 'overview', label: 'Overview', mark: 'O' },
  { id: 'users', label: 'Users', mark: 'U' },
  { id: 'skills', label: 'Shared Skills', mark: 'S' },
  { id: 'audit', label: 'Audit Log', mark: 'A' },
  { id: 'system', label: 'System', mark: '●' }
]

const section = ref('overview')
const loading = ref(false)
const error = ref('')
const notice = ref('')
const overview = ref({ users: {}, tasks: {}, recent_tasks: [] })
const users = ref([])
const usersTotal = ref(0)
const userQuery = ref('')
const selectedUser = ref(null)
const quotaDraft = reactive({})
const quotaReason = ref('')
const statusReason = ref('')
const workspaceTree = ref(null)
const selectedWorkspacePath = ref('')
const workspacePreview = ref(null)
const grantReason = ref('')
const activeGrant = ref(null)
const skills = ref([])
const skillEditor = reactive({ name: '', description: '', content: '', source: 'managed', persisted: false, versions: [] })
const skillValidation = ref(null)
const skillChangelog = ref('')
const auditLogs = ref([])
const systemHealth = ref(null)
const clock = ref(Date.now())
let clockTimer = null

const grantActive = computed(() => {
  clock.value
  return Boolean(activeGrant.value && new Date(activeGrant.value.expires_at).getTime() > Date.now())
})

const workspaceEntries = computed(() => {
  const entries = []
  const visit = (node) => {
    if (!node) return
    if (node.path || node.type !== 'dir') entries.push(node)
    node.children?.forEach(visit)
  }
  visit(workspaceTree.value)
  return entries
})

async function run(action) {
  loading.value = true
  error.value = ''
  notice.value = ''
  try { return await action() }
  catch (e) { error.value = e.message || String(e); return null }
  finally { loading.value = false }
}

function switchSection(next) {
  section.value = next
  if (next === 'overview') loadOverview()
  if (next === 'users') loadUsers()
  if (next === 'skills') loadSkills()
  if (next === 'audit') loadAudit()
  if (next === 'system') loadSystem()
}

async function loadOverview() {
  const data = await run(() => api.getAdminOverview())
  if (data) overview.value = data
}

async function cancelTask(task) {
  const result = await run(() => api.cancelAdminTask(task.trace_id, 'Cancelled from administrator task overview'))
  if (!result) return
  notice.value = `Task ${task.trace_id.slice(0, 8)} cancelled.`
  await loadOverview()
}

async function loadUsers() {
  const data = await run(() => api.listAdminUsers({ q: userQuery.value }))
  if (data) { users.value = data.items; usersTotal.value = data.total }
}

async function selectUser(userId) {
  const data = await run(() => api.getAdminUser(userId))
  if (!data) return
  selectedUser.value = data
  Object.assign(quotaDraft, data.quota)
  quotaReason.value = ''
  statusReason.value = ''
  workspaceTree.value = null
  workspacePreview.value = null
  selectedWorkspacePath.value = ''
  if (activeGrant.value?.target_user_id !== userId) activeGrant.value = null
}

async function toggleUserStatus() {
  const user = selectedUser.value?.user
  if (!user) return
  const updated = await run(() => api.updateAdminUserStatus(user.id, !user.is_active, statusReason.value))
  if (!updated) return
  selectedUser.value.user = updated
  statusReason.value = ''
  notice.value = `Account ${updated.is_active ? 'restored' : 'disabled'}.`
  await loadUsers()
}

async function revokeSessions() {
  const user = selectedUser.value?.user
  if (!user) return
  const result = await run(() => api.revokeAdminUserSessions(user.id, statusReason.value))
  if (!result) return
  statusReason.value = ''
  notice.value = `Revoked all sessions and ${result.api_keys_revoked} API key(s).`
}

async function saveQuota() {
  const userId = selectedUser.value?.user?.id
  if (!userId) return
  const payload = {
    daily_task_limit: quotaDraft.daily_task_limit,
    daily_token_limit: quotaDraft.daily_token_limit,
    monthly_token_limit: quotaDraft.monthly_token_limit,
    concurrent_task_limit: quotaDraft.concurrent_task_limit,
    workspace_bytes_limit: quotaDraft.workspace_bytes_limit,
    enabled: quotaDraft.enabled,
    expected_version: quotaDraft.version,
    reason: quotaReason.value
  }
  const updated = await run(() => api.updateAdminUserQuota(userId, payload))
  if (!updated) return
  Object.assign(quotaDraft, updated)
  selectedUser.value.quota = updated
  quotaReason.value = ''
  notice.value = 'Quota saved with an audit record.'
}

async function loadWorkspace() {
  const userId = selectedUser.value?.user?.id
  if (!userId) return
  const data = await run(() => api.getAdminWorkspaceTree(userId, '', 3))
  if (data) workspaceTree.value = data
}

async function chooseWorkspaceFile(node) {
  if (node.type !== 'file') return
  workspacePreview.value = null
  selectedWorkspacePath.value = node.path
  if (node.sensitive) {
    error.value = 'Sensitive files remain sealed even during temporary content access.'
    return
  }
  if (grantActive.value && activeGrant.value.target_user_id === selectedUser.value.user.id) await readSelectedWorkspaceFile()
}

async function grantAndRead() {
  const userId = selectedUser.value?.user?.id
  if (!userId || !selectedWorkspacePath.value) return
  const grant = await run(() => api.createAdminAccessGrant(userId, grantReason.value, 10))
  if (!grant) return
  activeGrant.value = grant
  grantReason.value = ''
  await readSelectedWorkspaceFile()
}

async function readSelectedWorkspaceFile() {
  const userId = selectedUser.value?.user?.id
  if (!userId || !grantActive.value) return
  const data = await run(() => api.readAdminWorkspaceFile(userId, selectedWorkspacePath.value, activeGrant.value.id))
  if (data) workspacePreview.value = data
}

async function loadSkills() {
  const data = await run(() => api.listAdminSkills())
  if (data) skills.value = data.items
}

function resetSkillEditor(value = {}) {
  Object.assign(skillEditor, { name: '', description: '', content: '', source: 'managed', persisted: false, status: 'draft', versions: [], ...value })
  skillValidation.value = null
  skillChangelog.value = ''
}

function newSkill() {
  resetSkillEditor({
    content: '---\nname: new-skill\ndescription: Explain what this guidance controls\n---\n\n# Managed Skill\n\nAdd precise, reusable guidance here.\n'
  })
}

async function selectSkill(skill) {
  if (skill.source === 'builtin') {
    resetSkillEditor({ ...skill, content: '', persisted: true, source: 'builtin' })
    return
  }
  const data = await run(() => api.getAdminSkill(skill.name))
  if (!data) return
  resetSkillEditor({ ...data, content: data.draft_content, persisted: true, source: 'managed' })
  skillValidation.value = data.validation
}

async function saveSkill() {
  const data = await run(() => api.saveAdminSkillDraft({ name: skillEditor.name, description: skillEditor.description, content: skillEditor.content }))
  if (!data) return
  skillEditor.persisted = true
  skillEditor.status = data.status
  skillValidation.value = data.validation
  notice.value = 'Shared Skill draft saved.'
  await loadSkills()
}

async function validateSkill() {
  const data = await run(() => api.validateAdminSkill(skillEditor.name))
  if (data) skillValidation.value = data
}

async function publishSkill() {
  const data = await run(() => api.publishAdminSkill(skillEditor.name, skillChangelog.value))
  if (!data) return
  notice.value = `Published ${data.name} v${data.version}.`
  skillChangelog.value = ''
  await loadSkills()
  await selectSkill({ name: data.name, source: 'managed' })
}

async function retireSkill() {
  const reason = skillChangelog.value || 'Retired from the administrator control room'
  const data = await run(() => api.retireAdminSkill(skillEditor.name, reason))
  if (!data) return
  notice.value = `${data.name} retired; historical versions remain available.`
  await loadSkills()
  await selectSkill({ name: data.name, source: 'managed' })
}

async function rollbackSkill(version) {
  const data = await run(() => api.rollbackAdminSkill(skillEditor.name, version, skillChangelog.value))
  if (!data) return
  notice.value = `Rolled ${data.name} back from v${version} as new v${data.version}.`
  skillChangelog.value = ''
  await loadSkills()
  await selectSkill({ name: data.name, source: 'managed' })
}

async function loadAudit() {
  const data = await run(() => api.listAdminAuditLogs())
  if (data) auditLogs.value = data.items
}

async function loadSystem() {
  const data = await run(() => api.getAdminSystemHealth())
  if (data) systemHealth.value = data
}

function formatNumber(value) { return new Intl.NumberFormat().format(Math.round(Number(value || 0))) }
function percent(value) { return `${Math.round(Number(value || 0) * 100)}%` }
function formatDuration(ms) { return ms ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s` : '—' }
function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
function formatDate(value) { return value ? new Date(value).toLocaleString() : '—' }
function formatTime(value) { return value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—' }

onMounted(() => {
  loadOverview()
  clockTimer = window.setInterval(() => { clock.value = Date.now() }, 1000)
})
onUnmounted(() => { if (clockTimer) window.clearInterval(clockTimer) })
</script>

<style scoped>
.admin-console { height: 100vh; overflow: hidden; background: #f8f9fb; color: #1a1a2e; display: flex; flex-direction: column; }
.admin-header { min-height: 112px; padding: 22px 30px 18px; background: #fff; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: flex-start; }
.admin-header h1 { margin: 2px 0 3px; font-size: 26px; letter-spacing: -.035em; }
.admin-header p { margin: 0; color: #6b7280; font-size: 13px; }
.admin-eyebrow, .section-heading span, .detail-kicker { color: #4f46e5; font: 700 10px/1.4 var(--font-mono); letter-spacing: .14em; }
.icon-button { width: 36px; height: 36px; border: 1px solid #dfe2e8; border-radius: 9px; background: #fff; color: #6b7280; font-size: 22px; cursor: pointer; }
.scope-bar { margin: 12px 18px 0; min-height: 54px; padding: 9px 14px; border: 1px solid #dfe3ed; border-left: 4px solid #0f9f7a; background: #fff; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 3px 16px rgba(30,35,60,.04); }
.scope-bar.elevated { border-color: #f2c36b; border-left-color: #c47a00; background: #fffaf0; }
.scope-state { display: flex; gap: 10px; align-items: center; }
.scope-state > div { display: grid; gap: 1px; }
.scope-state strong { font-size: 13px; }
.scope-state span { color: #6b7280; font-size: 12px; }
.scope-dot { width: 9px; height: 9px; border-radius: 50%; background: #0f9f7a; box-shadow: 0 0 0 4px #dff7ef; }
.elevated .scope-dot { background: #c47a00; box-shadow: 0 0 0 4px #fff0cf; }
.scope-bar code { font: 700 11px var(--font-mono); color: #667085; }
.admin-body { flex: 1; min-height: 0; display: grid; grid-template-columns: 190px minmax(0, 1fr); }
.admin-nav { padding: 18px 12px; border-right: 1px solid #e4e7ed; display: flex; flex-direction: column; gap: 4px; }
.admin-nav button { border: 0; background: transparent; color: #667085; border-radius: 8px; padding: 10px; display: flex; align-items: center; gap: 10px; font: 600 13px var(--font-ui); cursor: pointer; text-align: left; }
.admin-nav button:hover { background: #f0f1f6; }
.admin-nav button.active { color: #3730a3; background: #ececfa; }
.nav-mark { width: 22px; height: 22px; display: grid; place-items: center; border: 1px solid currentColor; border-radius: 6px; font: 700 10px var(--font-mono); }
.admin-content { min-width: 0; overflow-y: auto; padding: 22px 26px 70px; position: relative; }
.panel-stack { display: flex; flex-direction: column; gap: 16px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; min-height: 48px; }
.section-heading h2 { margin: 2px 0 0; font-size: 21px; letter-spacing: -.025em; }
.metric-strip { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); border: 1px solid #e0e3ea; background: #fff; }
.metric-strip article { padding: 16px 18px; display: grid; gap: 2px; border-right: 1px solid #eceef2; }
.metric-strip article:last-child { border-right: 0; }
.metric-strip span, .mini-metrics span, .health-card span, .storage-card span { color: #6b7280; font-size: 11px; text-transform: uppercase; letter-spacing: .07em; }
.metric-strip strong { font: 700 26px var(--font-mono); }
.metric-strip small, .storage-card small { color: #9ca3af; }
.metric-strip .risk strong { color: #c47a00; }
.data-panel { background: #fff; border: 1px solid #e0e3ea; border-radius: 10px; box-shadow: 0 2px 10px rgba(30,35,60,.025); }
.panel-title { min-height: 56px; padding: 13px 16px; border-bottom: 1px solid #eceef2; display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.panel-title h3 { margin: 0; font-size: 14px; }
.panel-title span { color: #8a92a2; font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { padding: 9px 13px; color: #8a92a2; background: #fafbfc; text-align: left; font: 600 10px var(--font-ui); text-transform: uppercase; letter-spacing: .06em; }
td { padding: 11px 13px; border-top: 1px solid #eff1f4; vertical-align: middle; }
td code, .task-title + code { color: #8a92a2; font: 11px var(--font-mono); }
.task-title { display: block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-pill { display: inline-flex; border-radius: 999px; padding: 3px 7px; background: #edf0f5; color: #667085; font: 700 9px var(--font-mono); text-transform: uppercase; }
.status-pill.succeeded { color: #08785d; background: #e1f6ef; }
.status-pill.failed { color: #b22d2d; background: #fdeaea; }
.status-pill.running, .status-pill.pending { color: #9a5d00; background: #fff1d2; }
.notice { margin-bottom: 12px; padding: 10px 12px; border-radius: 8px; display: flex; justify-content: space-between; font-size: 12px; }
.notice.error { color: #a62e2e; background: #fdecec; border: 1px solid #f7caca; }
.notice.success { color: #08785d; background: #e5f7f0; border: 1px solid #bcebdc; }
.notice button { border: 0; color: inherit; background: transparent; cursor: pointer; }
.loading-line { height: 2px; background: #e8e9f2; overflow: hidden; position: sticky; top: 0; z-index: 3; }
.loading-line span { display: block; width: 35%; height: 100%; background: #4f46e5; animation: loading 1s infinite ease-in-out; }
@keyframes loading { from { transform: translateX(-100%); } to { transform: translateX(400%); } }
button, input, textarea { font-family: var(--font-ui); }
.primary-button, .secondary-button, .danger-button, .warning-button, .search-box button { border-radius: 7px; padding: 8px 12px; font-size: 12px; font-weight: 700; cursor: pointer; }
.primary-button { border: 1px solid #4f46e5; background: #4f46e5; color: #fff; }
.secondary-button { border: 1px solid #d7dbe4; background: #fff; color: #3f4655; }
.danger-button { border: 1px solid #efb4b4; background: #fff2f2; color: #b22d2d; }
.warning-button { border: 1px solid #e1a741; background: #fff6e3; color: #915900; }
button:disabled { opacity: .45; cursor: not-allowed; }
.search-box { display: flex; }
.search-box input { width: 230px; border: 1px solid #d7dbe4; border-radius: 7px 0 0 7px; padding: 8px 10px; outline: none; }
.search-box button { border: 1px solid #4f46e5; border-radius: 0 7px 7px 0; background: #4f46e5; color: #fff; }
.users-layout { display: grid; grid-template-columns: 310px minmax(0,1fr); gap: 16px; align-items: start; }
.user-directory { max-height: calc(100vh - 270px); overflow-y: auto; }
.user-row { width: 100%; border: 0; border-top: 1px solid #eff1f4; background: #fff; padding: 10px 12px; display: grid; grid-template-columns: 34px minmax(0,1fr) auto; gap: 9px; align-items: center; text-align: left; cursor: pointer; }
.user-row:hover, .user-row.selected { background: #f5f5fb; }
.avatar { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; background: #ececfa; color: #4f46e5; font-weight: 800; }
.user-copy { display: grid; min-width: 0; }
.user-copy strong { font-size: 12px; }
.user-copy small { color: #8a92a2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.profile-card { padding: 16px; display: flex; justify-content: space-between; align-items: center; }
.profile-card h3 { margin: 3px 0 2px; font-size: 20px; }
.profile-card p { margin: 0; color: #737b8c; font-size: 12px; }
.mini-metrics { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 8px; }
.mini-metrics article { border: 1px solid #e3e6ec; background: #fff; border-radius: 8px; padding: 11px 12px; display: grid; }
.mini-metrics strong { font: 700 17px var(--font-mono); }
.form-panel { padding-bottom: 14px; }
.form-panel > label, .editor-label { margin: 12px 16px; }
label { display: grid; gap: 5px; color: #667085; font-size: 11px; font-weight: 600; }
input, textarea { border: 1px solid #d7dbe4; border-radius: 7px; padding: 8px 9px; color: #25283b; background: #fff; outline: none; }
input:focus, textarea:focus { border-color: #7770e7; box-shadow: 0 0 0 3px #eeeeff; }
.form-panel > button { margin-left: 16px; }
.quota-toggle { display: flex; align-items: center; gap: 8px; color: #4f5668; font-size: 12px; }
.quota-toggle input { width: auto; }
.form-grid { padding: 13px 16px; display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 10px; }
.form-grid.two { grid-template-columns: 1fr 2fr; padding-left: 0; padding-right: 0; }
.workspace-panel { overflow: hidden; }
.workspace-grid { display: grid; grid-template-columns: minmax(240px, .8fr) minmax(300px, 1.2fr); min-height: 300px; }
.workspace-tree { padding: 9px; border-right: 1px solid #eceef2; max-height: 420px; overflow: auto; }
.workspace-tree button { width: 100%; border: 0; background: transparent; border-radius: 5px; padding: 6px 7px; display: grid; grid-template-columns: 14px minmax(0,1fr) auto; text-align: left; gap: 5px; color: #555d6d; font-size: 11px; cursor: pointer; }
.workspace-tree button:not(:disabled):hover { background: #f0f1f7; color: #3932ba; }
.workspace-tree button:disabled { opacity: 1; }
.workspace-tree button.sensitive { color: #b22d2d; }
.workspace-tree small { color: #a1a7b2; }
.guarded-preview { padding: 14px; min-width: 0; }
.guarded-preview p { color: #737b8c; font-size: 12px; }
.guarded-preview textarea { width: 100%; resize: vertical; margin-bottom: 9px; }
.preview-heading { padding-bottom: 9px; border-bottom: 1px solid #eceef2; }
.preview-heading code { color: #4f46e5; font: 11px var(--font-mono); }
.guarded-preview pre { max-height: 360px; overflow: auto; margin: 10px 0 0; white-space: pre-wrap; word-break: break-word; color: #35394b; font: 11px/1.55 var(--font-mono); }
.empty-state, .empty-detail { padding: 30px; color: #969dac; text-align: center; font-size: 12px; }
.skills-layout { display: grid; grid-template-columns: 280px minmax(0,1fr); gap: 16px; align-items: start; }
.skill-list { overflow: hidden; }
.skill-list button { width: 100%; border: 0; border-bottom: 1px solid #eef0f3; background: #fff; padding: 11px 12px; display: flex; justify-content: space-between; gap: 8px; text-align: left; cursor: pointer; }
.skill-list button:hover, .skill-list button.selected { background: #f4f4fb; }
.skill-list button > span:first-child { min-width: 0; display: grid; }
.skill-list strong { font: 700 12px var(--font-mono); }
.skill-list small { color: #8a92a2; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.skill-editor { padding: 0 16px 16px; }
.skill-editor .panel-title { margin: 0 -16px 13px; }
.editor-label { margin: 0; }
.editor-label textarea { width: 100%; resize: vertical; font: 11px/1.5 var(--font-mono); }
.validation-box { margin: 11px 0; border: 1px solid #bcebdc; background: #effaf6; color: #08785d; border-radius: 7px; padding: 9px 11px; display: grid; gap: 3px; font-size: 11px; }
.validation-box.invalid { border-color: #f2bcbc; background: #fff0f0; color: #a62e2e; }
.validation-box ul { margin: 2px 0 0 17px; }
.skill-actions { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; }
.skill-actions input { flex: 1; min-width: 180px; }
.version-list { margin-top: 12px; padding-top: 10px; border-top: 1px solid #eceef2; display: flex; flex-direction: column; gap: 5px; color: #737b8c; font-size: 11px; }
.version-list > span { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.version-list button { border: 1px solid #d9dce5; background: #fff; border-radius: 5px; padding: 3px 7px; color: #575d6d; cursor: pointer; font-size: 10px; }
.version-list button:disabled { cursor: not-allowed; opacity: .45; }
.table-action { border: 1px solid #dfe2e8; border-radius: 5px; background: #fff; padding: 4px 7px; cursor: pointer; font-size: 10px; }
.table-action.danger { color: #b22d2d; border-color: #efcaca; }
.version-list code { color: #4f46e5; }
.system-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.health-card, .storage-card { padding: 16px; display: grid; gap: 5px; }
.health-card strong { font: 700 17px var(--font-mono); text-transform: uppercase; }
.health-card strong.ok { color: #0f9f7a; }
.health-card strong.error { color: #d14343; }
.storage-card { grid-column: span 2; }
.storage-card strong { font: 700 16px var(--font-mono); }
@media (max-width: 1050px) {
  .metric-strip { grid-template-columns: repeat(2,1fr); }
  .metric-strip article:nth-child(2) { border-right: 0; }
  .users-layout, .skills-layout { grid-template-columns: 1fr; }
  .user-directory { max-height: 300px; }
}
@media (max-width: 760px) {
  .admin-header { padding: 16px; }
  .scope-bar { margin: 8px; }
  .admin-body { grid-template-columns: 58px minmax(0,1fr); }
  .admin-nav { padding: 10px 7px; }
  .admin-nav button { justify-content: center; }
  .admin-nav button span:last-child { display: none; }
  .admin-content { padding: 15px 10px 50px; }
  .form-grid, .mini-metrics, .system-grid { grid-template-columns: repeat(2,1fr); }
  .workspace-grid { grid-template-columns: 1fr; }
  .workspace-tree { border-right: 0; border-bottom: 1px solid #eceef2; }
}
@media (prefers-reduced-motion: reduce) { .loading-line span { animation: none; width: 100%; } }
</style>
