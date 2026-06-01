<template>
  <div class="memory-viewer">
    <header class="viewer-header">
      <div class="header-left">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <h2>Long-Term Memory</h2>
      </div>
      <div class="header-tabs">
        <button :class="{ active: tab === 'conversations' }" @click="tab = 'conversations'">Conversations</button>
        <button :class="{ active: tab === 'patterns' }" @click="tab = 'patterns'">Patterns</button>
      </div>
      <button class="btn-refresh" @click="loadData" :disabled="loading" title="Refresh">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
      </button>
    </header>

    <div class="viewer-body">
      <div v-if="loading" class="status">Loading memories...</div>
      <div v-else-if="error" class="status error">{{ error }}</div>

      <!-- Conversations tab -->
      <template v-else-if="tab === 'conversations'">
        <div v-if="!memories.length" class="empty">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
          <p>No memories yet</p>
          <span>Conversations will be summarized and stored here over time</span>
        </div>
        <div v-else class="memory-list">
          <div v-for="(m, i) in memories" :key="memUid(m, i)" class="memory-card" :class="{ deleting: deletingId === memUid(m, i) }">
            <!-- Normal view -->
            <template v-if="confirmingId !== memUid(m, i)">
              <div class="card-header">
                <span class="importance-stars">
                  <template v-for="s in 5" :key="s">
                    <svg width="12" height="12" viewBox="0 0 24 24" :fill="s <= stars(m.importance) ? 'var(--accent)' : 'none'" :stroke="s <= stars(m.importance) ? 'var(--accent)' : 'var(--text-tertiary)'" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
                  </template>
                </span>
                <span class="importance-label">{{ (m.importance * 100).toFixed(0) }}%</span>
                <span class="card-meta">
                  <template v-if="m.rounds">{{ m.rounds }} rounds · </template>
                  <template v-if="m.has_tool_actions">🔧 · </template>
                  {{ formatDate(m.timestamp) }}
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
              <div class="card-content">{{ m.content }}</div>
              <div v-if="m.session_id" class="card-footer">Session: {{ m.session_id.slice(0, 8) }}</div>
            </template>
            <!-- Confirm delete view -->
            <template v-else>
              <div class="confirm-delete">
                <p class="confirm-text">Delete this memory?</p>
                <p class="confirm-hint">This action cannot be undone.</p>
                <div class="confirm-actions">
                  <button class="btn-cancel" @click="confirmingId = null" :disabled="deletingId !== null">Cancel</button>
                  <button class="btn-confirm-delete" @click="handleDelete(m.id, i)" :disabled="deletingId !== null">
                    {{ deletingId === memUid(m, i) ? 'Deleting...' : 'Delete' }}
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <!-- Patterns tab -->
      <template v-else-if="tab === 'patterns'">
        <div v-if="!patterns.length" class="empty">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
          <p>No patterns learned yet</p>
          <span>The system learns your preferences and workflows over time</span>
        </div>
        <div v-else class="pattern-list">
          <div v-for="(p, i) in patterns" :key="patUid(p, i)" class="pattern-card" :class="{ deleting: deletingPatternId === patUid(p, i) }">
            <!-- Normal view -->
            <template v-if="confirmingPatternId !== patUid(p, i)">
              <span :class="['pattern-badge', p.pattern_type]">{{ p.pattern_type }}</span>
              <span class="pattern-key">{{ p.pattern_key }}</span>
              <span class="pattern-confidence">{{ (p.confidence * 100).toFixed(0) }}% confidence</span>
              <button
                class="btn-delete-sm"
                title="Delete this pattern"
                @click.stop="confirmingPatternId = patUid(p, i)"
                :disabled="deletingPatternId !== null"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </template>
            <!-- Confirm delete view -->
            <template v-else>
              <span class="confirm-text-sm">Delete this pattern?</span>
              <button class="btn-cancel-sm" @click="confirmingPatternId = null" :disabled="deletingPatternId !== null">Cancel</button>
              <button class="btn-confirm-delete-sm" @click="handleDeletePattern(p.id, i)" :disabled="deletingPatternId !== null">
                {{ deletingPatternId === patUid(p, i) ? '...' : 'Delete' }}
              </button>
            </template>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as api from '../api/client.js'

const tab = ref('conversations')
const loading = ref(false)
const error = ref('')
const memories = ref([])
const patterns = ref([])

// Delete state
const confirmingId = ref(null)
const deletingId = ref(null)
const confirmingPatternId = ref(null)
const deletingPatternId = ref(null)

function stars(importance) {
  if (importance >= 0.8) return 5
  if (importance >= 0.6) return 4
  if (importance >= 0.4) return 3
  if (importance >= 0.2) return 2
  return 1
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return iso }
}

// Guaranteed-unique card identifiers. ChromaDB ids may be empty for
// legacy data, so fall back to index to ensure each card is distinct.
function memUid(m, i) { return m.id || `mem-${i}` }
function patUid(p, i) { return p.id || `pat-${i}` }

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [mem, pat] = await Promise.all([
      api.fetchMemories(50),
      api.fetchPatterns()
    ])
    memories.value = mem.memories || []
    patterns.value = pat.patterns || []
  } catch (e) {
    error.value = 'Failed to load memories: ' + (e.message || 'Unknown error')
  } finally {
    loading.value = false
  }
}

async function handleDelete(docId, index) {
  const uid = memUid({ id: docId }, index)
  if (!docId) {
    // Fallback: if no id, remove from local state only
    memories.value.splice(index, 1)
    confirmingId.value = null
    return
  }
  deletingId.value = uid
  try {
    await api.deleteMemory(docId)
    memories.value.splice(index, 1)
    confirmingId.value = null
  } catch (e) {
    error.value = 'Failed to delete: ' + (e.message || 'Unknown error')
  } finally {
    deletingId.value = null
  }
}

async function handleDeletePattern(patternId, index) {
  const uid = patUid({ id: patternId }, index)
  if (!patternId) {
    patterns.value.splice(index, 1)
    confirmingPatternId.value = null
    return
  }
  deletingPatternId.value = uid
  try {
    await api.deletePattern(patternId)
    patterns.value.splice(index, 1)
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

.header-tabs {
  display: flex;
  gap: 2px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  padding: 2px;
}

.header-tabs button {
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

.empty p { font-size: var(--text-base); font-weight: 500; color: var(--text-secondary); margin: 0; }
.empty span { font-size: var(--text-sm); }

/* Memory cards */
.memory-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 800px;
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

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.importance-stars { display: flex; gap: 1px; }
.importance-label { font-size: var(--text-xs); font-weight: 600; color: var(--accent); }

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
  font-size: var(--text-sm);
  color: var(--text-primary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.card-footer {
  margin-top: 8px;
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* Patterns */
.pattern-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 800px;
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

.pattern-key {
  flex: 1;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
}

.pattern-confidence {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
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
</style>
