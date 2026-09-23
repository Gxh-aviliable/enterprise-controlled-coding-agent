<template>
  <section class="task-changes" aria-label="Task changes">
    <button class="changes-toggle" @click="toggle" :aria-expanded="open">Task changes & validation</button>
    <div v-if="open">
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="loading">Loading recorded changes…</p>
      <template v-if="data">
        <p class="coverage">Text recovery: up to 256 KB per file, 100 files and 4 MB per task, retained for 7 days. Binary files, links, sensitive files and detected secrets are excluded. Network and database effects cannot be restored.</p>
        <p v-if="data.pending_execution" role="status">An execution is unresolved. Its effects need verification.</p>
        <p v-if="!data.files.length">No recorded file changes.</p>
        <details v-for="file in data.files" :key="file.path">
          <summary><label><input type="checkbox" v-model="selected" :value="file.path" :disabled="!file.restorable || busy" @click.stop />{{ file.operation }} · {{ file.path }}</label></summary>
          <p v-if="file.conflict">Changed since this task. Recovery is blocked.</p>
          <p v-else-if="!file.supported">Diff and recovery unavailable: snapshot missing, excluded, expired, over budget, or interrupted by another edit.</p>
          <pre v-if="file.diff !== null"><template v-if="file.diff"><span v-for="(line, index) in diffLines(file.diff)" :key="index" :class="diffLineClass(line, index)">{{ line }}</span></template><template v-else>No text difference</template></pre>
        </details>
        <details v-for="check in data.validations" :key="check.execution_id" class="validation">
          <summary>{{ check.validation.kind }} · {{ !check.current ? 'Stale input version' : check.ok ? 'Passed on recorded input' : 'Unconfirmed / failed' }}</summary>
          <pre>{{ check.command }}</pre>
          <p>cwd: {{ check.cwd }} · exit: {{ check.exit_code }} · cancelled: {{ check.cancelled }} · timeout: {{ check.timed_out }}</p>
          <p class="version">Input {{ check.input_version }}</p>
        </details>
        <p v-if="!data.validations.length">No recognized validation was recorded.</p>
        <p v-if="data.restore" role="status">Last recovery: {{ data.restore.status }}. Applied: {{ data.restore.applied_paths.join(', ') || 'none recorded' }}.<span v-if="data.restore.attempting_path"> Interrupted at {{ data.restore.attempting_path }}; inspect its current contents.</span></p>
        <button v-if="!confirming" @click="confirming = true" :disabled="!selected.length || busy || data.pending_execution">Review file recovery</button>
        <div v-else class="recovery-confirm">
          <p>Restore these {{ selected.length }} files to their contents before this task? All selected files must still match the task’s final version. Multiple files are restored individually.</p>
          <ul><li v-for="path in selected" :key="path">{{ path }}</li></ul>
          <button @click="restore" :disabled="busy">{{ busy ? 'Restoring…' : 'Restore selected files' }}</button>
          <button @click="confirming = false" :disabled="busy">Cancel</button>
        </div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { ref } from 'vue'
import * as api from '../api/client.js'
const props = defineProps({ traceId: { type: String, required: true } })
const open = ref(false), loading = ref(false), busy = ref(false), confirming = ref(false)
const data = ref(null), error = ref(''), selected = ref([])
function diffLines(diff) {
  return diff.match(/[^\n]*\n|[^\n]+$/g) || []
}
function diffLineClass(line, index) {
  if ((index === 0 && line.startsWith('--- ')) || (index === 1 && line.startsWith('+++ '))) return 'diff-meta'
  if (line.startsWith('@@')) return 'diff-hunk'
  if (line.startsWith('+')) return 'diff-added'
  if (line.startsWith('-')) return 'diff-removed'
  return ''
}
async function load() {
  loading.value = true
  try { data.value = await api.getTaskChanges(props.traceId); selected.value = [] }
  catch (err) { error.value = err.message }
  finally { loading.value = false }
}
async function toggle() {
  open.value = !open.value
  if (open.value) { error.value = ''; await load() }
}
async function restore() {
  busy.value = true; error.value = ''
  try { await api.restoreTaskFiles(props.traceId, selected.value, data.value.version); confirming.value = false }
  catch (err) { error.value = err.message }
  finally { await load(); busy.value = false }
}
</script>
<style scoped>
.task-changes { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 12px 16px; font-size: 13px; line-height: 1.6; color: var(--text-primary); background: var(--bg-primary); }
.changes-toggle { font-weight: 600; border: 0; background: transparent; padding: 0; color: var(--accent); text-align: left; }
button { padding: 7px 11px; cursor: pointer; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg-secondary); color: var(--text-primary); font: inherit; }
button:hover:not(:disabled) { background: var(--bg-hover); }
button:disabled { cursor: default; opacity: .5; }
details { margin: 8px 0; }
summary { cursor: pointer; overflow-wrap: anywhere; }
pre { overflow: auto; max-height: 350px; padding: 10px; border-radius: var(--radius-sm); background: var(--bg-secondary); font-family: var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.diff-added { color: var(--task-diff-added, #166534); background: rgba(34, 197, 94, 0.12); }
.diff-removed { color: var(--task-diff-removed, #b91c1c); background: rgba(239, 68, 68, 0.12); }
.diff-meta { color: var(--text-tertiary); }
.diff-hunk { color: var(--accent); }
:global([data-theme="dark"]) { --task-diff-added: #86efac; --task-diff-removed: #fca5a5; }
.coverage, .version { color: var(--text-secondary); overflow-wrap: anywhere; }
.recovery-confirm { border-top: 1px solid var(--border); margin-top: 8px; }
</style>
