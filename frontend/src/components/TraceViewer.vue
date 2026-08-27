<template>
  <section class="trace-viewer">
    <header class="trace-header">
      <div>
        <div class="trace-eyebrow">Agent operations</div>
        <h2>Execution trace</h2>
        <p>Find the exact node, tool, or budget that determined a task result.</p>
      </div>
      <button class="refresh-button" :disabled="loading" @click="loadDashboard">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
        </svg>
        Refresh evidence
      </button>
    </header>

    <div class="metrics-strip" aria-label="Task metrics">
      <div v-for="metric in metricCards" :key="metric.label" class="metric-cell">
        <span>{{ metric.label }}</span>
        <strong>{{ metric.value }}</strong>
        <small>{{ metric.hint }}</small>
      </div>
    </div>

    <div v-if="error" class="trace-error" role="alert">
      <strong>Trace data could not be loaded.</strong>
      <span>{{ error }}</span>
    </div>

    <div class="trace-workspace">
      <aside class="run-index" aria-label="Task runs">
        <div class="index-heading">
          <span>Recorded runs</span>
          <span class="run-count">{{ tasks.length }}</span>
        </div>

        <div v-if="loading && !tasks.length" class="index-empty">Loading task evidence…</div>
        <div v-else-if="!tasks.length" class="index-empty">
          <strong>No traces yet</strong>
          <span>Run a chat task. Its node and tool evidence will appear here.</span>
        </div>
        <button
          v-for="task in tasks"
          :key="task.trace_id"
          :class="['run-row', { selected: selectedId === task.trace_id }]"
          @click="selectTrace(task.trace_id)"
        >
          <span :class="['status-pip', task.status]"></span>
          <span class="run-copy">
            <strong>{{ task.request_summary || 'Untitled task' }}</strong>
            <span>{{ formatDate(task.started_at) }} · {{ formatDuration(task.duration_ms) }}</span>
          </span>
          <span class="run-mode">{{ task.mode === 'multi_agent' ? 'multi' : 'single' }}</span>
        </button>
      </aside>

      <main class="trace-detail">
        <div v-if="traceLoading" class="detail-empty">Loading ordered events…</div>
        <div v-else-if="!selectedTrace" class="detail-empty">
          <div class="empty-rail" aria-hidden="true"><i></i><i></i><i></i></div>
          <strong>Select a task to replay it</strong>
          <span>The timeline keeps model summaries and tool evidence, with secrets redacted.</span>
        </div>
        <template v-else>
          <div class="detail-heading">
            <div>
              <div class="trace-id">TRACE / {{ selectedTrace.trace_id }}</div>
              <h3>{{ selectedTrace.request_summary }}</h3>
            </div>
            <span :class="['status-badge', selectedTrace.status]">{{ humanStatus(selectedTrace.status) }}</span>
          </div>

          <div class="run-facts">
            <span><b>{{ selectedTrace.events.length }}</b> events</span>
            <span><b>{{ selectedTrace.metrics.model_calls }}</b> model calls</span>
            <span><b>{{ selectedTrace.metrics.tool_calls }}</b> tools</span>
            <span><b>{{ selectedTrace.metrics.memory_injected || 0 }}</b> memories injected</span>
            <span><b>{{ compactNumber(selectedTrace.metrics.total_tokens) }}</b> tokens</span>
            <span><b>{{ formatDuration(selectedTrace.duration_ms) }}</b> elapsed</span>
          </div>

          <div class="execution-spine">
            <article
              v-for="event in selectedTrace.events"
              :key="event.event_id"
              :class="['event-row', event.status, `type-${event.type}`]"
            >
              <div class="event-node" aria-hidden="true"></div>
              <div class="event-card">
                <div class="event-topline">
                  <span class="event-type">{{ event.type }}</span>
                  <strong>{{ eventTitle(event) }}</strong>
                  <time>{{ formatTime(event.timestamp) }}</time>
                </div>
                <div class="event-meta">
                  <span>{{ event.status }}</span>
                  <span v-if="event.duration_ms">{{ formatDuration(event.duration_ms) }}</span>
                  <span v-if="event.data.phase">phase: {{ event.data.phase }}</span>
                  <span v-if="event.data.error_code">{{ event.data.error_code }}</span>
                </div>
                <div v-if="event.duration_ms" class="duration-track" aria-hidden="true">
                  <i :style="durationStyle(event.duration_ms)"></i>
                </div>
                <div v-if="event.type === 'memory'" class="recall-receipt">
                  <div class="receipt-heading">
                    <span>Recall receipt</span>
                    <strong>{{ event.data.injected_count || 0 }} / {{ memoryCandidates(event).length }} injected</strong>
                  </div>
                  <div class="receipt-query">
                    <span>Query</span>
                    <p>{{ event.data.query_summary || 'No query recorded' }}</p>
                  </div>
                  <div class="receipt-facts">
                    <span>strategy <b>{{ event.data.strategy || 'unknown' }}</b></span>
                    <span>threshold <b>{{ formatDistance(event.data.threshold) }}</b></span>
                    <span>context <b>{{ compactNumber(event.data.injected_tokens) }} tokens</b></span>
                    <span>application <b>{{ event.data.application_status || 'not attributed' }}</b></span>
                  </div>
                  <div v-if="memoryCandidates(event).length" class="candidate-table">
                    <div class="candidate-row candidate-header">
                      <span>rank</span><span>record</span><span>type</span><span>distance / lexical</span><span>decision</span>
                    </div>
                    <div
                      v-for="candidate in memoryCandidates(event)"
                      :key="`${candidate.collection}-${candidate.memory_id}`"
                      :class="['candidate-row', candidate.eligible ? 'eligible' : 'filtered']"
                    >
                      <span>#{{ candidate.rank ?? '—' }}</span>
                      <code :title="candidate.memory_id">{{ shortId(candidate.memory_id) }}</code>
                      <span>{{ candidate.collection }} / {{ candidate.memory_type }}</span>
                      <span>{{ formatDistance(candidate.distance) }} / {{ formatDistance(candidate.lexical_score) }}</span>
                      <strong>{{ candidate.eligible ? 'injected' : candidate.filter_reason }}</strong>
                    </div>
                  </div>
                  <p class="attribution-note">
                    Injected is not proof of application. This trace confirms retrieval and context injection only.
                  </p>
                </div>
                <template v-else>
                  <p v-if="eventSummary(event)" class="event-summary">{{ eventSummary(event) }}</p>
                  <details v-if="hasDetails(event.data)" class="event-details">
                    <summary>Inspect recorded data</summary>
                    <pre>{{ prettyData(event.data) }}</pre>
                  </details>
                </template>
              </div>
            </article>
          </div>
        </template>
      </main>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import * as api from '../api/client.js'

const loading = ref(false)
const traceLoading = ref(false)
const error = ref('')
const tasks = ref([])
const metrics = ref({})
const selectedId = ref('')
const selectedTrace = ref(null)

const metricCards = computed(() => [
  { label: 'Task success', value: percent(metrics.value.task_success_rate), hint: `${metrics.value.succeeded || 0}/${metrics.value.task_count || 0} terminal runs` },
  { label: 'Tool success', value: percent(metrics.value.tool_success_rate), hint: `${metrics.value.tool_calls || 0} invocations` },
  { label: 'Mean latency', value: formatDuration(metrics.value.average_duration_ms), hint: 'terminal tasks only' },
  { label: 'Mean tokens', value: compactNumber(metrics.value.average_tokens), hint: 'provider-reported' },
  { label: 'Memory-injected runs', value: percent(metrics.value.memory_injection_rate), hint: `${metrics.value.memory_injected || 0} records · ${compactNumber(metrics.value.average_memory_tokens)} avg tokens` },
  { label: 'Human involved', value: percent(metrics.value.human_intervention_rate), hint: `${metrics.value.confirmation_count || 0} confirmations` },
  { label: 'Safety blocks', value: compactNumber(metrics.value.safety_interceptions), hint: 'policy interceptions' }
])

const maxEventDuration = computed(() => {
  if (!selectedTrace.value?.events?.length) return 1
  return Math.max(1, ...selectedTrace.value.events.map(event => event.duration_ms || 0))
})

function percent(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`
}

function compactNumber(value) {
  const number = Number(value) || 0
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}m`
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}k`
  return String(Math.round(number))
}

function formatDuration(value) {
  const ms = Number(value) || 0
  if (!ms) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

function formatDate(value) {
  if (!value) return 'unknown time'
  return new Date(value).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function humanStatus(status) {
  return String(status || 'unknown').replaceAll('_', ' ')
}

function eventTitle(event) {
  return String(event.name || 'event').replaceAll('_', ' ')
}

function memoryCandidates(event) {
  return Array.isArray(event?.data?.candidates) ? event.data.candidates : []
}

function shortId(value) {
  const text = String(value || 'unknown')
  return text.length > 14 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text
}

function formatDistance(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(3) : '—'
}

function eventSummary(event) {
  const data = event.data || {}
  return data.output_summary || data.input_summary || data.message || data.error || data.reason || ''
}

function hasDetails(data) {
  if (!data) return false
  return Object.keys(data).some(key => !['phase', 'output_summary', 'input_summary', 'message', 'error', 'reason'].includes(key))
}

function prettyData(data) {
  return JSON.stringify(data, null, 2)
}

function durationStyle(duration) {
  const width = Math.max(2, Math.min(100, ((Number(duration) || 0) / maxEventDuration.value) * 100))
  return { width: `${width}%` }
}

async function selectTrace(traceId) {
  selectedId.value = traceId
  traceLoading.value = true
  error.value = ''
  try {
    selectedTrace.value = await api.replayTaskTrace(traceId)
  } catch (e) {
    error.value = e.message || 'Unknown trace error'
    selectedTrace.value = null
  } finally {
    traceLoading.value = false
  }
}

async function loadDashboard() {
  loading.value = true
  error.value = ''
  try {
    const [taskData, metricData] = await Promise.all([
      api.listTaskRuns(50),
      api.getTaskMetrics()
    ])
    tasks.value = taskData.tasks || []
    metrics.value = metricData || {}
    const nextId = tasks.value.some(task => task.trace_id === selectedId.value)
      ? selectedId.value
      : tasks.value[0]?.trace_id
    if (nextId) await selectTrace(nextId)
    else selectedTrace.value = null
  } catch (e) {
    error.value = e.message || 'Unknown trace error'
  } finally {
    loading.value = false
  }
}

onMounted(loadDashboard)
</script>

<style scoped>
.trace-viewer {
  --trace-success: #0f766e;
  --trace-waiting: #b45309;
  --trace-failed: #be123c;
  --trace-info: #4f46e5;
  --trace-pausing: #d97706;
  --trace-paused: #7c3aed;
  --trace-resuming: #0284c7;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

.trace-header {
  min-height: 92px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border-light);
}

.trace-eyebrow,
.trace-id {
  color: var(--accent);
  font: 600 11px/1.2 var(--font-mono);
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.trace-header h2 {
  margin: 3px 0 1px;
  font-size: var(--text-xl);
  letter-spacing: -0.02em;
}

.trace-header p {
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 34px;
  padding: 7px 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-primary);
  font: 600 var(--text-sm)/1 var(--font-ui);
  cursor: pointer;
}

.refresh-button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.refresh-button:focus-visible, .run-row:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.refresh-button:disabled { opacity: 0.45; cursor: wait; }

.metrics-strip {
  display: grid;
  grid-template-columns: repeat(7, minmax(105px, 1fr));
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}

.metric-cell {
  min-width: 0;
  padding: 11px 16px 10px;
  border-right: 1px solid var(--border-light);
}

.metric-cell:last-child { border-right: 0; }
.metric-cell span, .metric-cell small { display: block; }
.metric-cell span { color: var(--text-secondary); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
.metric-cell strong { display: block; margin: 2px 0 1px; font: 600 20px/1.15 var(--font-mono); color: var(--text-primary); }
.metric-cell small { overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

.trace-error {
  display: flex;
  gap: 8px;
  padding: 9px 18px;
  border-bottom: 1px solid rgba(190, 18, 60, 0.25);
  background: rgba(190, 18, 60, 0.08);
  color: var(--trace-failed);
  font-size: var(--text-sm);
}

.trace-workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
}

.run-index {
  min-height: 0;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  background: var(--bg-secondary);
}

.index-heading {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 8px;
  background: var(--bg-secondary);
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.run-count { font-family: var(--font-mono); }
.index-empty, .detail-empty { color: var(--text-tertiary); font-size: var(--text-sm); }
.index-empty { display: flex; flex-direction: column; gap: 4px; padding: 34px 18px; }
.index-empty strong, .detail-empty strong { color: var(--text-secondary); }

.run-row {
  width: calc(100% - 16px);
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: start;
  gap: 9px;
  margin: 2px 8px;
  padding: 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.run-row:hover { background: var(--bg-hover); }
.run-row.selected { border-color: var(--border); background: var(--bg-primary); box-shadow: var(--shadow-sm); }
.status-pip { width: 7px; height: 7px; margin-top: 5px; border-radius: 50%; background: var(--text-tertiary); }
.status-pip.succeeded { background: var(--trace-success); }
.status-pip.failed { background: var(--trace-failed); }
.status-pip.waiting_confirmation { background: var(--trace-waiting); }
.status-pip.running, .status-pip.pending { background: var(--trace-info); box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.12); }
.status-pip.pause_requested { background: var(--trace-pausing); box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.14); animation: trace-status-pulse 1.2s ease-in-out infinite; }
.status-pip.paused { background: var(--bg-primary); border: 2px solid var(--trace-paused); }
.status-pip.resuming { background: var(--trace-resuming); box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.14); animation: trace-status-pulse 1.2s ease-in-out infinite; }
@keyframes trace-status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.run-copy { min-width: 0; }
.run-copy strong, .run-copy span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-copy strong { color: var(--text-primary); font-size: var(--text-sm); font-weight: 600; }
.run-copy span { margin-top: 2px; color: var(--text-tertiary); font: 11px/1.4 var(--font-mono); }
.run-mode { padding: 2px 4px; color: var(--text-tertiary); font: 10px/1.2 var(--font-mono); }

.trace-detail { min-width: 0; min-height: 0; overflow-y: auto; padding: 20px 24px 52px; }
.detail-empty { min-height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; text-align: center; }
.empty-rail { display: flex; gap: 18px; margin-bottom: 12px; }
.empty-rail i { width: 9px; height: 9px; border: 2px solid var(--accent); border-radius: 50%; position: relative; }
.empty-rail i:not(:last-child)::after { content: ''; position: absolute; top: 2px; left: 9px; width: 18px; height: 1px; background: var(--border); }

.detail-heading { display: flex; justify-content: space-between; gap: 20px; padding-bottom: 13px; border-bottom: 1px solid var(--border-light); }
.detail-heading h3 { max-width: 780px; margin-top: 5px; font-size: var(--text-lg); line-height: 1.35; }
.status-badge { align-self: flex-start; padding: 4px 8px; border: 1px solid var(--border); border-radius: 999px; color: var(--text-secondary); font: 600 11px/1 var(--font-mono); text-transform: uppercase; }
.status-badge.succeeded { border-color: rgba(15, 118, 110, 0.3); color: var(--trace-success); }
.status-badge.failed, .status-badge.cancelled { border-color: rgba(190, 18, 60, 0.3); color: var(--trace-failed); }
.status-badge.running, .status-badge.pending { border-color: rgba(79, 70, 229, 0.3); color: var(--trace-info); }
.status-badge.waiting_confirmation { border-color: rgba(180, 83, 9, 0.3); color: var(--trace-waiting); }
.status-badge.pause_requested { border-color: rgba(217, 119, 6, 0.35); background: rgba(217, 119, 6, 0.07); color: var(--trace-pausing); }
.status-badge.paused { border-color: rgba(124, 58, 237, 0.35); background: rgba(124, 58, 237, 0.07); color: var(--trace-paused); }
.status-badge.resuming { border-color: rgba(2, 132, 199, 0.35); background: rgba(2, 132, 199, 0.07); color: var(--trace-resuming); }

.run-facts { display: flex; flex-wrap: wrap; gap: 14px 24px; padding: 10px 0 16px; color: var(--text-tertiary); font-size: var(--text-xs); }
.run-facts b { color: var(--text-primary); font-family: var(--font-mono); }

.execution-spine { --rail-x: 9px; position: relative; }
.execution-spine::before { content: ''; position: absolute; top: 9px; bottom: 15px; left: var(--rail-x); width: 1px; background: var(--border); }
.event-row { position: relative; display: grid; grid-template-columns: 19px minmax(0, 1fr); gap: 12px; padding-bottom: 10px; }
.event-node { z-index: 1; width: 9px; height: 9px; margin: 11px 0 0 5px; border: 2px solid var(--bg-primary); border-radius: 50%; background: var(--text-tertiary); box-shadow: 0 0 0 1px var(--border); }
.event-row.success .event-node, .event-row.succeeded .event-node { background: var(--trace-success); }
.event-row.error .event-node, .event-row.failed .event-node, .event-row.blocked .event-node { background: var(--trace-failed); }
.event-row.waiting .event-node, .event-row.rejected .event-node { background: var(--trace-waiting); }
.event-row.pause_requested .event-node { background: var(--trace-pausing); }
.event-row.paused .event-node { background: var(--bg-primary); border-color: var(--trace-paused); box-shadow: 0 0 0 1px var(--trace-paused); }
.event-row.resuming .event-node { background: var(--trace-resuming); }
.type-model .event-node { border-radius: 2px; background: var(--trace-info); transform: rotate(45deg); }

.event-card { min-width: 0; padding: 8px 11px 9px; border: 1px solid var(--border-light); border-radius: var(--radius-sm); background: var(--bg-secondary); }
.event-card:hover { border-color: var(--border); }
.event-topline { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.event-topline strong { overflow: hidden; color: var(--text-primary); font-size: var(--text-sm); text-overflow: ellipsis; white-space: nowrap; }
.event-topline time { margin-left: auto; color: var(--text-tertiary); font: 10px/1 var(--font-mono); }
.event-type { flex-shrink: 0; min-width: 54px; color: var(--accent); font: 700 9px/1 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; }
.event-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 4px 0 0 62px; color: var(--text-tertiary); font: 10px/1.35 var(--font-mono); }
.duration-track { height: 2px; margin: 7px 0 0 62px; overflow: hidden; background: var(--border-light); }
.duration-track i { display: block; height: 100%; background: var(--accent); opacity: 0.65; }
.event-summary { margin: 7px 0 0 62px; color: var(--text-secondary); font-size: var(--text-xs); line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.event-details { margin: 7px 0 0 62px; }
.event-details summary { color: var(--text-tertiary); font-size: 11px; cursor: pointer; }
.event-details pre { max-height: 260px; margin-top: 6px; overflow: auto; padding: 9px; border-radius: 4px; background: var(--bg-tertiary); color: var(--text-secondary); font: 11px/1.5 var(--font-mono); white-space: pre-wrap; word-break: break-word; }

.recall-receipt {
  margin: 9px 0 2px 62px;
  overflow: hidden;
  border: 1px solid rgba(79, 70, 229, 0.2);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
}

.receipt-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-light);
  background: rgba(79, 70, 229, 0.05);
}

.receipt-heading span {
  color: var(--trace-info);
  font: 700 10px/1 var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.receipt-heading strong {
  color: var(--text-primary);
  font: 700 11px/1 var(--font-mono);
}

.receipt-query {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  gap: 8px;
  padding: 9px 10px 6px;
}

.receipt-query span {
  color: var(--text-tertiary);
  font: 700 10px/1.45 var(--font-mono);
  text-transform: uppercase;
}

.receipt-query p {
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: 1.45;
}

.receipt-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  padding: 0 10px 9px 74px;
  color: var(--text-tertiary);
  font: 10px/1.35 var(--font-mono);
}

.receipt-facts b { color: var(--text-secondary); }

.candidate-table {
  border-top: 1px solid var(--border-light);
}

.candidate-row {
  display: grid;
  grid-template-columns: 42px minmax(100px, 0.85fr) minmax(150px, 1.4fr) 72px minmax(100px, 0.75fr);
  align-items: center;
  gap: 8px;
  min-height: 31px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--border-light);
  color: var(--text-secondary);
  font: 10px/1.35 var(--font-mono);
}

.candidate-row:last-child { border-bottom: 0; }
.candidate-row code { overflow: hidden; color: var(--trace-info); text-overflow: ellipsis; white-space: nowrap; }
.candidate-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.candidate-row.eligible strong { color: var(--trace-success); }
.candidate-row.filtered { color: var(--text-tertiary); background: var(--bg-secondary); }
.candidate-row.filtered strong { color: var(--trace-waiting); }

.candidate-header {
  min-height: 25px;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.attribution-note {
  margin: 0;
  padding: 8px 10px;
  border-top: 1px solid var(--border-light);
  color: var(--text-tertiary);
  font-size: 10px;
  line-height: 1.45;
}

@media (max-width: 1050px) {
  .metrics-strip { grid-template-columns: repeat(4, 1fr); }
  .metric-cell:nth-child(4) { border-right: 0; }
  .trace-workspace { grid-template-columns: 260px minmax(0, 1fr); }
}

@media (max-width: 760px) {
  .trace-header { align-items: flex-start; }
  .trace-header p { display: none; }
  .refresh-button { font-size: 0; }
  .trace-workspace { display: flex; flex-direction: column; overflow-y: auto; }
  .run-index { max-height: 210px; border-right: 0; border-bottom: 1px solid var(--border); }
  .trace-detail { overflow: visible; padding: 16px; }
  .recall-receipt { margin-left: 0; }
  .candidate-row { grid-template-columns: 34px minmax(90px, 0.8fr) minmax(120px, 1.2fr) 60px minmax(90px, 0.8fr); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
</style>
