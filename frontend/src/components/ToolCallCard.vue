<template>
  <article :class="['tool-card', status]" :data-tool-status="status">
    <button
      type="button"
      class="tool-header"
      :aria-expanded="expanded"
      :aria-label="`${name}: ${stateLabel}. ${expanded ? 'Hide' : 'Show'} details`"
      @click="toggle"
    >
      <span class="tool-status-icon" aria-hidden="true">
        <span v-if="status === 'running'" class="spinner"></span>
        <svg v-else-if="status === 'waiting'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5l3 1.8"/></svg>
        <svg v-else-if="status === 'done'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.7"><polyline points="19 7 10 16 5 11"/></svg>
        <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.7"><line x1="17" y1="7" x2="7" y2="17"/><line x1="7" y1="7" x2="17" y2="17"/></svg>
      </span>

      <span class="tool-copy">
        <span class="tool-kicker">Tool</span>
        <span class="tool-name">{{ name }}</span>
      </span>

      <span class="tool-state" aria-live="polite">{{ stateLabel }}</span>
      <svg :class="['chevron', { rotated: expanded }]" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
    </button>

    <div v-show="expanded" class="tool-body">
      <div class="tool-body-label">Execution output</div>
      <div v-if="result" class="tool-output">
        <pre>{{ result }}</pre>
      </div>
      <div v-else-if="error" class="tool-error">{{ error }}</div>
      <div v-else class="tool-waiting">
        {{ status === 'waiting' ? 'Waiting for approval before this tool can run.' : 'No output has been received yet.' }}
      </div>
      <div v-if="result && error" class="tool-error">{{ error }}</div>
    </div>
  </article>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  status: { type: String, default: 'running' },  // 'running' | 'waiting' | 'done' | 'error'
  result: { type: String, default: '' },
  error: { type: String, default: '' },
  duration: { type: Number, default: null }
})

const expanded = ref(false)

const durationLabel = computed(() => {
  const milliseconds = Number(props.duration)
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return ''
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`
  return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)} s`
})

const stateLabel = computed(() => {
  if (props.status === 'running') return 'Running'
  if (props.status === 'waiting') return 'Approval needed'
  if (props.status === 'done') return durationLabel.value || 'Complete'
  return 'Failed'
})

function toggle() {
  expanded.value = !expanded.value
}
</script>

<style scoped>
.tool-card {
  --tool-accent: #64748b;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg-primary);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
  width: 100%;
  overflow: hidden;
}

.tool-card.running { --tool-accent: #d97706; }
.tool-card.waiting { --tool-accent: #4f46e5; }
.tool-card.done { --tool-accent: #059669; }
.tool-card.error { --tool-accent: #dc2626; }

.tool-card:hover {
  border-color: color-mix(in srgb, var(--tool-accent) 34%, var(--border));
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.07);
}

.tool-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 7px 10px;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-ui);
  text-align: left;
  cursor: pointer;
}

.tool-header:focus-visible {
  outline: 2px solid var(--tool-accent);
  outline-offset: -2px;
}

.tool-status-icon {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 8px;
  background: var(--bg-tertiary);
  background: color-mix(in srgb, var(--tool-accent) 11%, var(--bg-primary));
  color: var(--tool-accent);
}

.spinner {
  width: 15px;
  height: 15px;
  border: 2px solid var(--tool-accent);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.tool-copy {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.tool-kicker {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tool-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-state {
  flex-shrink: 0;
  padding: 3px 7px;
  border-radius: 999px;
  background: var(--bg-tertiary);
  background: color-mix(in srgb, var(--tool-accent) 9%, var(--bg-primary));
  color: var(--tool-accent);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 650;
}

.chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s ease;
}

.chevron.rotated {
  transform: rotate(180deg);
}

.tool-body {
  padding: 10px 12px 12px 48px;
  border-top: 1px solid var(--border-light);
}

.tool-body-label {
  margin-bottom: 7px;
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tool-output {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-light);
  border-radius: 7px;
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
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-error {
  color: #dc2626;
  font-size: var(--text-sm);
  padding: 6px 0;
}

.tool-waiting {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  padding: 6px 0;
}

@media (max-width: 560px) {
  .tool-kicker {
    display: none;
  }

  .tool-state {
    max-width: 116px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-body {
    padding-left: 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tool-card {
    transition: none;
  }

  .spinner {
    animation: none;
    opacity: 0.75;
  }
}
</style>
