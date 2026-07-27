<template>
  <div class="memory-viewer">
    <header class="viewer-header">
      <div class="header-left">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <div>
          <h2>Memory Ledger</h2>
          <p>Only trusted, reusable context is recalled by the Agent.</p>
        </div>
      </div>
      <div class="header-tabs">
        <button :class="{ active: tab === 'conversations' }" @click="tab = 'conversations'">
          Task outcomes <span class="tab-count">{{ visibleMemories.length }}</span>
        </button>
        <button :class="{ active: tab === 'patterns' }" @click="tab = 'patterns'">
          Preferences <span class="tab-count">{{ visiblePatterns.length }}</span>
        </button>
      </div>
      <button class="btn-refresh" @click="loadData" :disabled="loading" title="Refresh">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
      </button>
    </header>

    <div class="viewer-body">
      <div v-if="loading" class="status">Loading memories...</div>
      <div v-else-if="error" class="status error">{{ error }}</div>
      <template v-else>
        <div v-if="actionNotice" class="action-notice" role="status">
          {{ actionNotice }}
        </div>
        <section class="memory-health" aria-label="Memory retrieval evidence">
          <div class="health-cell active-memory">
            <span class="health-label">Recall-ready records</span>
            <strong>{{ activeTotal }}</strong>
            <span>{{ activeMemoryCount }} outcomes + {{ activePatternCount }} preferences</span>
          </div>
          <div class="health-cell recalled-memory">
            <span class="health-label">Recalled</span>
            <strong>{{ recalledActiveTotal }}</strong>
            <span>injected at least once</span>
          </div>
          <div class="health-cell dormant-memory">
            <span class="health-label">Never recalled</span>
            <strong>{{ neverRecalledActiveTotal }}</strong>
            <span>stored but not yet useful</span>
          </div>
          <div class="health-cell legacy-memory">
            <span class="health-label">Legacy quarantine</span>
            <strong>{{ legacyTotal }}</strong>
            <span>{{ legacyMemoryCount }} outcomes + {{ legacyPatternCount }} preferences</span>
          </div>
        </section>
        <div class="policy-note">
          <span class="policy-mark">Evidence rule</span>
          <p>
            Recalled means the record was selected and injected into model context. It is evidence of retrieval,
            not proof that the model used it correctly.
          </p>
        </div>

        <div class="quality-toolbar">
          <span>Show</span>
          <div class="quality-switch" role="group" aria-label="Filter memory quality">
            <button
              v-for="option in qualityOptions"
              :key="option.value"
              :class="{ active: qualityFilter === option.value }"
              @click="qualityFilter = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <template v-if="tab === 'conversations'">
          <div v-if="!visibleMemories.length" class="empty">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
            <p>No {{ qualityFilter }} task outcomes</p>
            <span v-if="qualityFilter === 'active'">New trusted memories appear only after the admission policy accepts a completed engineering task.</span>
            <span v-else>Nothing matches this quality filter.</span>
            <button v-if="activePatternCount && qualityFilter === 'active'" class="empty-action" @click="tab = 'patterns'">
              Open {{ activePatternCount }} active preferences
            </button>
            <button v-if="qualityFilter === 'active' && legacyMemoryCount" class="empty-action" @click="qualityFilter = 'legacy'">
              Review {{ legacyMemoryCount }} legacy records
            </button>
          </div>
          <div v-else class="memory-list">
            <div
              v-for="(m, i) in visibleMemories"
              :key="memUid(m, i)"
              class="memory-card"
              :class="[`quality-${m.quality_status}`, { deleting: deletingId === memUid(m, i) }]"
            >
              <template v-if="confirmingId !== memUid(m, i)">
                <div class="card-header">
                  <span :class="['quality-badge', m.quality_status]">{{ m.quality_status }}</span>
                  <span class="memory-type">{{ humanize(m.memory_type) }}</span>
                  <span class="card-meta">
                    {{ Math.round((m.importance || 0) * 100) }}% importance · {{ formatDate(m.timestamp) }}
                  </span>
                  <button
                    class="btn-delete"
                    title="Delete this memory"
                    @click.stop="confirmingId = memUid(m, i)"
                    :disabled="deletingId !== null"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                  </button>
                </div>
                <div class="memory-explanation">
                  <span>{{ m.quality_status === 'active' ? 'Admitted because' : 'Quarantined because' }}</span>
                  <strong>{{ reasonLabel(m.admission_reason) }}</strong>
                </div>
                <div class="recall-evidence">
                  <span :class="['recall-state', retrievalCount(m) ? 'recalled' : 'never']">
                    {{ retrievalCount(m) ? `${retrievalCount(m)} recalls` : 'Never recalled' }}
                  </span>
                  <span v-if="m.last_retrieved_at">last {{ formatDate(m.last_retrieved_at) }}</span>
                  <span v-else-if="m.quality_status === 'active'">no task has selected this record</span>
                  <span v-if="m.retrieval_enabled === false">retrieval disabled</span>
                </div>
                <details class="memory-details">
                  <summary>{{ m.content_format === 'atomic_note' ? 'View durable note' : 'View stored summary' }}</summary>
                  <div class="card-content">{{ m.content }}</div>
                </details>
                <div class="card-footer">
                  <span v-if="m.session_id">session {{ m.session_id.slice(0, 8) }}</span>
                  <span>schema v{{ m.schema_version || 1 }}</span>
                  <span v-if="m.content_format">{{ humanize(m.content_format) }}</span>
                  <span v-if="m.task_status !== 'unknown'">{{ m.task_status }}</span>
                </div>
              </template>
              <template v-else>
                <div class="confirm-delete">
                  <p class="confirm-text">Delete this memory?</p>
                  <p class="confirm-hint">This action cannot be undone.</p>
                  <div class="confirm-actions">
                    <button class="btn-cancel" @click="confirmingId = null" :disabled="deletingId !== null">Cancel</button>
                    <button class="btn-confirm-delete" @click="handleDelete(m)" :disabled="deletingId !== null">
                      {{ deletingId === memUid(m, i) ? 'Deleting...' : 'Delete' }}
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </template>

        <template v-else>
          <div v-if="!visiblePatterns.length" class="empty">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
            <p>No {{ qualityFilter }} preferences</p>
            <span>Only explicit, durable preference statements can enter Active recall.</span>
            <button v-if="activeMemoryCount && qualityFilter === 'active'" class="empty-action" @click="tab = 'conversations'">
              Open {{ activeMemoryCount }} active task outcomes
            </button>
            <button v-if="qualityFilter === 'active' && legacyPatternCount" class="empty-action" @click="qualityFilter = 'legacy'">
              Review {{ legacyPatternCount }} legacy preferences
            </button>
          </div>
          <div v-else class="pattern-list">
            <div
              v-for="(p, i) in visiblePatterns"
              :key="patUid(p, i)"
              class="pattern-card"
              :class="[`quality-${p.quality_status}`, { deleting: deletingPatternId === patUid(p, i) }]"
            >
              <template v-if="confirmingPatternId !== patUid(p, i)">
                <span :class="['quality-badge', p.quality_status]">{{ p.quality_status }}</span>
                <span :class="['pattern-badge', p.pattern_type]">{{ p.pattern_type }}</span>
                <span class="pattern-copy">
                  <strong>{{ humanize(p.pattern_key) }}</strong>
                  <small>{{ patternDescription(p) }}</small>
                  <small v-if="p.quality_status === 'legacy'" class="pattern-provenance">
                    Quarantined: {{ reasonLabel(p.quarantine_reason || 'legacy_unclassified') }}
                  </small>
                  <small v-else-if="p.source_memory_ids?.length" class="pattern-provenance">
                    Derived from {{ p.source_memory_ids.length }} source {{ p.source_memory_ids.length === 1 ? 'memory' : 'memories' }}
                  </small>
                </span>
                <span class="pattern-confidence">
                  {{ Math.round((p.confidence || 0) * 100) }}% · {{ p.evidence_count || 0 }} evidence
                </span>
                <span :class="['pattern-recall', retrievalCount(p) ? 'recalled' : 'never']">
                  {{ retrievalCount(p) ? `${retrievalCount(p)} recalls` : 'Never recalled' }}
                </span>
                <button
                  class="btn-delete-sm"
                  title="Delete this pattern"
                  @click.stop="confirmingPatternId = patUid(p, i)"
                  :disabled="deletingPatternId !== null"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </template>
              <template v-else>
                <span class="confirm-text-sm">Delete this pattern?</span>
                <button class="btn-cancel-sm" @click="confirmingPatternId = null" :disabled="deletingPatternId !== null">Cancel</button>
                <button class="btn-confirm-delete-sm" @click="handleDeletePattern(p)" :disabled="deletingPatternId !== null">
                  {{ deletingPatternId === patUid(p, i) ? '...' : 'Delete' }}
                </button>
              </template>
            </div>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import * as api from '../api/client.js'

const tab = ref('conversations')
const loading = ref(false)
const error = ref('')
const actionNotice = ref('')
const memories = ref([])
const patterns = ref([])
const qualityFilter = ref('active')
const qualityOptions = [
  { value: 'active', label: 'Active' },
  { value: 'legacy', label: 'Legacy' },
  { value: 'all', label: 'All' }
]

const activeMemoryCount = computed(() => memories.value.filter(item => item.quality_status === 'active').length)
const legacyMemoryCount = computed(() => memories.value.length - activeMemoryCount.value)
const activePatternCount = computed(() => patterns.value.filter(item => item.quality_status === 'active').length)
const legacyPatternCount = computed(() => patterns.value.length - activePatternCount.value)
const activeTotal = computed(() => activeMemoryCount.value + activePatternCount.value)
const legacyTotal = computed(() => legacyMemoryCount.value + legacyPatternCount.value)
const recalledActiveTotal = computed(() => {
  const activeRecords = [...memories.value, ...patterns.value]
    .filter(item => item.quality_status === 'active')
  return activeRecords.filter(item => retrievalCount(item) > 0).length
})
const neverRecalledActiveTotal = computed(() => activeTotal.value - recalledActiveTotal.value)
const visibleMemories = computed(() => filterByQuality(memories.value))
const visiblePatterns = computed(() => filterByQuality(patterns.value))

const confirmingId = ref(null)
const deletingId = ref(null)
const confirmingPatternId = ref(null)
const deletingPatternId = ref(null)

function filterByQuality(items) {
  if (qualityFilter.value === 'all') return items
  return items.filter(item => item.quality_status === qualityFilter.value)
}

function humanize(value) {
  return String(value || 'unknown').replaceAll('_', ' ')
}

function reasonLabel(reason) {
  const labels = {
    verified_engineering_outcome: 'successful engineering task with durable evidence',
    explicit_user_request: 'the user explicitly requested durable storage',
    legacy_unclassified: 'created before the v2 admission policy',
    missing_source_provenance: 'its source memory cannot be verified'
  }
  return labels[reason] || humanize(reason)
}

function retrievalCount(record) {
  return Math.max(0, Number(record?.retrieval_count ?? record?.access_count) || 0)
}

function patternDescription(pattern) {
  if (pattern.value) {
    try {
      const parsed = JSON.parse(pattern.value)
      return Object.values(parsed)
        .map(value => typeof value === 'string' ? value : JSON.stringify(value))
        .join(' · ')
    } catch {
      return pattern.value
    }
  }
  const text = pattern.text || ''
  return text.includes(' = ') ? text.split(' = ', 2)[1] : text
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

function memUid(memory, index) { return memory.id || `mem-${index}` }
function patUid(pattern, index) { return pattern.id || `pat-${index}` }

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [memoryResponse, patternResponse] = await Promise.all([
      api.fetchMemories(200),
      api.fetchPatterns()
    ])
    memories.value = memoryResponse.memories || []
    patterns.value = patternResponse.patterns || []
  } catch (e) {
    error.value = 'Failed to load memories: ' + (e.message || 'Unknown error')
  } finally {
    loading.value = false
  }
}

async function handleDelete(memory) {
  const docId = memory.id
  if (!docId) {
    memories.value = memories.value.filter(item => item !== memory)
    confirmingId.value = null
    return
  }
  deletingId.value = docId
  try {
    const receipt = await api.deleteMemory(docId)
    confirmingId.value = null
    await loadData()
    const linkedCount = Number(receipt?.deleted_pattern_count) || 0
    actionNotice.value = linkedCount
      ? `Deleted the memory and ${linkedCount} linked ${linkedCount === 1 ? 'preference' : 'preferences'}.`
      : 'Deleted the memory.'
  } catch (e) {
    error.value = 'Failed to delete: ' + (e.message || 'Unknown error')
  } finally {
    deletingId.value = null
  }
}

async function handleDeletePattern(pattern) {
  const patternId = pattern.id
  if (!patternId) {
    patterns.value = patterns.value.filter(item => item !== pattern)
    confirmingPatternId.value = null
    return
  }
  deletingPatternId.value = patternId
  try {
    await api.deletePattern(patternId)
    patterns.value = patterns.value.filter(item => item.id !== patternId)
    confirmingPatternId.value = null
  } catch (e) {
    error.value = 'Failed to delete: ' + (e.message || 'Unknown error')
  } finally {
    deletingPatternId.value = null
  }
}

onMounted(loadData)
</script>

<style scoped>
.memory-viewer {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  min-height: 0;
}

.viewer-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-left h2 {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.header-left p {
  margin: 2px 0 0;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.header-tabs {
  display: flex;
  gap: 2px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  padding: 2px;
}

.header-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 14px;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
}

.header-tabs button.active {
  background: var(--bg-primary);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

.tab-count {
  min-width: 18px;
  padding: 2px 5px;
  border-radius: 999px;
  background: var(--bg-hover);
  color: var(--text-tertiary);
  font: 700 10px/1 var(--font-mono);
  text-align: center;
}

.header-tabs button.active .tab-count {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
}

.btn-refresh {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
}

.btn-refresh:hover:not(:disabled) { background: var(--bg-hover); }
.btn-refresh:disabled { opacity: 0.5; }

/* Body */
.viewer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.viewer-body::-webkit-scrollbar { width: 6px; }
.viewer-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

.status, .empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
  color: var(--text-tertiary);
  gap: 8px;
}

.error { color: #ef4444; }

.action-notice {
  max-width: 1100px;
  margin-bottom: 10px;
  padding: 9px 12px;
  border: 1px solid #a7e5cf;
  border-radius: var(--radius-sm);
  background: #effcf7;
  color: #087a55;
  font-size: var(--text-xs);
}

.empty p { font-size: var(--text-base); font-weight: 500; color: var(--text-secondary); margin: 0; }
.empty span { font-size: var(--text-sm); }

.empty-action {
  margin-top: 8px;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--bg-primary);
  font: 500 var(--text-xs) var(--font-ui);
  cursor: pointer;
}

.empty-action:hover { border-color: var(--accent); color: var(--accent); }

.memory-health {
  display: grid;
  grid-template-columns: repeat(4, minmax(145px, 1fr));
  max-width: 1100px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--bg-secondary);
}

.health-cell {
  min-height: 88px;
  padding: 14px 16px;
}

.health-cell {
  display: grid;
  grid-template-columns: auto 1fr;
  align-content: center;
  column-gap: 10px;
  border-right: 1px solid var(--border-light);
}

.health-cell strong {
  grid-row: 1 / 3;
  grid-column: 1;
  align-self: center;
  font: 700 28px/1 var(--font-mono);
  letter-spacing: -0.06em;
}

.health-cell > span:last-child {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

.health-label {
  font: 700 10px/1.2 var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.active-memory strong,
.active-memory .health-label { color: #0f9f6e; }
.recalled-memory strong,
.recalled-memory .health-label { color: #0f766e; }
.dormant-memory strong,
.dormant-memory .health-label { color: var(--text-secondary); }
.legacy-memory strong,
.legacy-memory .health-label { color: #c47a16; }

.policy-note {
  max-width: 1100px;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
}

.policy-note p {
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: 1.5;
}

.policy-mark {
  flex: 0 0 auto;
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 4px 8px;
  color: var(--accent);
  font: 700 10px/1 var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.quality-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 1100px;
  margin: 16px 0 10px;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.quality-switch {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
}

.quality-switch button {
  padding: 5px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  font: 600 var(--text-xs) var(--font-ui);
  cursor: pointer;
}

.quality-switch button.active {
  background: var(--bg-primary);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

/* Memory cards */
.memory-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1100px;
}

.memory-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  transition: border-color var(--transition), opacity 0.2s;
  position: relative;
}

.memory-card:hover { border-color: var(--border); }
.memory-card.deleting { opacity: 0.5; pointer-events: none; }
.memory-card.quality-active { border-left: 3px solid #10b981; }
.memory-card.quality-legacy {
  border-left: 3px solid #d9932f;
  border-top-style: dashed;
  border-right-style: dashed;
  border-bottom-style: dashed;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.quality-badge,
.memory-type {
  border-radius: 999px;
  padding: 3px 7px;
  font: 700 10px/1 var(--font-mono);
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.quality-badge.active { background: #dff8ee; color: #087a55; }
.quality-badge.legacy { background: #fff1d8; color: #9a5d0b; }
.memory-type { background: var(--bg-tertiary); color: var(--text-secondary); }

.card-meta {
  flex: 1;
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

/* Delete button — hidden until hover */
.btn-delete {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  cursor: pointer;
  opacity: 0;
  transition: all var(--transition);
  flex-shrink: 0;
}

.memory-card:hover .btn-delete {
  opacity: 1;
}

.btn-delete:focus-visible,
.btn-delete-sm:focus-visible { opacity: 1; outline: 2px solid var(--accent); outline-offset: 2px; }

.btn-delete:hover {
  color: #ef4444;
  background: #fef2f2;
  border-color: #fecaca;
}

.btn-delete:disabled { opacity: 0.3; pointer-events: none; }

/* Confirm delete */
.confirm-delete {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
}

.confirm-text {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.confirm-hint {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin: 0;
}

.confirm-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.btn-cancel, .btn-confirm-delete {
  padding: 6px 16px;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
  border: 1px solid var(--border);
}

.btn-cancel {
  background: var(--bg-primary);
  color: var(--text-secondary);
}

.btn-cancel:hover { background: var(--bg-hover); }

.btn-confirm-delete {
  background: #ef4444;
  color: #fff;
  border-color: #ef4444;
}

.btn-confirm-delete:hover { background: #dc2626; }
.btn-confirm-delete:disabled { opacity: 0.5; pointer-events: none; }

.card-content {
  margin-top: 10px;
  font-size: var(--text-sm);
  color: var(--text-primary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.memory-explanation {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.memory-explanation strong {
  color: var(--text-secondary);
  font-weight: 600;
}

.recall-evidence {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 9px;
  color: var(--text-tertiary);
  font: 11px/1.35 var(--font-mono);
}

.recall-state,
.pattern-recall {
  padding: 3px 7px;
  border-radius: 999px;
  font: 700 10px/1 var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.recall-state.recalled,
.pattern-recall.recalled {
  background: #dff8ee;
  color: #087a55;
}

.recall-state.never,
.pattern-recall.never {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}

.memory-details {
  margin-top: 10px;
  border-top: 1px solid var(--border-light);
  padding-top: 8px;
}

.memory-details summary {
  width: fit-content;
  color: var(--accent);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
}

.card-footer {
  margin-top: 8px;
  display: flex;
  gap: 10px;
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* Patterns */
.pattern-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 1100px;
}

.pattern-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  position: relative;
}

.pattern-card.deleting { opacity: 0.5; pointer-events: none; }
.pattern-card.quality-active { border-left: 3px solid #10b981; }
.pattern-card.quality-legacy { border-left: 3px solid #d9932f; border-style: dashed; }

.pattern-badge {
  font-size: var(--text-xs);
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: capitalize;
}

.pattern-badge.preference { background: #eef2ff; color: #4f46e5; }
.pattern-badge.workflow { background: #ecfdf5; color: #059669; }
.pattern-badge.shortcut { background: #fffbeb; color: #d97706; }

.pattern-copy {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pattern-copy strong {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.pattern-copy small {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pattern-copy .pattern-provenance {
  color: #a16413;
  font-family: var(--font-mono);
}

.pattern-confidence {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

.pattern-recall {
  flex-shrink: 0;
}

/* Pattern delete button */
.btn-delete-sm {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: var(--text-tertiary);
  cursor: pointer;
  opacity: 0;
  transition: all var(--transition);
  flex-shrink: 0;
}

.pattern-card:hover .btn-delete-sm { opacity: 1; }
.btn-delete-sm:hover { color: #ef4444; background: #fef2f2; }
.btn-delete-sm:disabled { opacity: 0.3; pointer-events: none; }

/* Pattern confirm delete (inline) */
.confirm-text-sm {
  font-size: var(--text-sm);
  font-weight: 500;
  color: #ef4444;
}

.btn-cancel-sm, .btn-confirm-delete-sm {
  padding: 3px 10px;
  border-radius: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  border: 1px solid var(--border);
  transition: all var(--transition);
}

.btn-cancel-sm {
  background: var(--bg-primary);
  color: var(--text-secondary);
}

.btn-cancel-sm:hover { background: var(--bg-hover); }

.btn-confirm-delete-sm {
  background: #ef4444;
  color: #fff;
  border-color: #ef4444;
}

.btn-confirm-delete-sm:hover { background: #dc2626; }
.btn-confirm-delete-sm:disabled { opacity: 0.5; pointer-events: none; }

@media (max-width: 860px) {
  .viewer-header { flex-wrap: wrap; }
  .header-tabs { order: 3; width: 100%; }
  .header-tabs button { flex: 1; }
  .memory-health { grid-template-columns: 1fr 1fr; }
  .health-cell:nth-child(2n) { border-right: 0; }
  .health-cell:nth-child(-n + 2) { border-bottom: 1px solid var(--border-light); }
}

@media (max-width: 620px) {
  .viewer-body { padding: 14px; }
  .memory-health { grid-template-columns: 1fr; }
  .health-cell { border-right: 0; border-bottom: 1px solid var(--border-light); }
  .health-cell:last-child { border-bottom: 0; }
  .policy-note { align-items: flex-start; flex-direction: column; }
  .card-header { flex-wrap: wrap; }
  .card-meta { flex-basis: 100%; order: 5; }
  .pattern-card { align-items: flex-start; flex-wrap: wrap; }
  .pattern-copy { flex-basis: calc(100% - 150px); }
}
</style>
