<template>
  <div :class="['tool-card', status]" @click="toggle">
    <div class="tool-header">
      <span class="tool-status-icon">
        <span v-if="status === 'running'" class="spinner"></span>
        <svg v-else-if="status === 'waiting'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2.5"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
        <svg v-else-if="status === 'done'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
        <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </span>
      <span class="tool-label">
        <span class="tool-name">{{ name }}</span>
        <span v-if="status === 'running'" class="tool-state running-text">running…</span>
        <span v-else-if="status === 'waiting'" class="tool-state waiting-text">awaiting approval</span>
        <span v-else-if="status === 'done'" class="tool-state done-text">{{ duration ? duration + 'ms' : 'done' }}</span>
        <span v-else class="tool-state error-text">failed</span>
      </span>
      <svg :class="['chevron', { rotated: expanded }]" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
    </div>

    <div v-show="expanded" class="tool-body">
      <div v-if="result" class="tool-output">
        <pre>{{ result }}</pre>
      </div>
      <div v-if="error" class="tool-error">{{ error }}</div>
      <div v-if="!result && !error && ['running', 'waiting'].includes(status)" class="tool-waiting">
        {{ status === 'waiting' ? 'Waiting for human approval…' : 'Waiting for output…' }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  name: { type: String, required: true },
  status: { type: String, default: 'running' },  // 'running' | 'waiting' | 'done' | 'error'
  result: { type: String, default: '' },
  error: { type: String, default: '' },
  duration: { type: Number, default: null }
})

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
}
</script>

<style scoped>
.tool-card {
  margin: 8px 0;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-secondary);
  cursor: pointer;
  transition: border-color var(--transition);
  max-width: 100%;
  overflow: hidden;
}

.tool-card.running { border-left: 3px solid #f59e0b; }
.tool-card.waiting { border-left: 3px solid #6366f1; }
.tool-card.done { border-left: 3px solid #10b981; }
.tool-card.error { border-left: 3px solid #ef4444; }

.tool-card:hover {
  border-color: var(--text-tertiary);
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}

.tool-status-icon {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #f59e0b;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.tool-label {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tool-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.tool-state {
  font-size: var(--text-xs);
  font-weight: 500;
}

.running-text { color: #f59e0b; }
.waiting-text { color: #6366f1; }
.done-text { color: #10b981; }
.error-text { color: #ef4444; }

.chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s ease;
}

.chevron.rotated {
  transform: rotate(180deg);
}

.tool-body {
  padding: 0 12px 10px 34px;
}

.tool-output {
  background: var(--bg-tertiary);
  border-radius: 4px;
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
}

.tool-output pre {
  margin: 0;
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-error {
  color: #ef4444;
  font-size: var(--text-sm);
  padding: 6px 0;
}

.tool-waiting {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-style: italic;
  padding: 6px 0;
}
</style>
