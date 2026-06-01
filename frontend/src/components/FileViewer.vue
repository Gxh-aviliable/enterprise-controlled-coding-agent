<template>
  <div class="file-viewer">
    <header class="viewer-header">
      <button class="btn-back" @click="$emit('close')" title="Back to chat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M19 12H5M12 19l-7-7 7-7"/>
        </svg>
        Back
      </button>
      <div class="file-info">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
        </svg>
        <span class="file-name">{{ file?.name || 'Unknown file' }}</span>
        <span v-if="file" class="file-path">{{ file.path }}</span>
      </div>
      <button class="btn-download" @click="downloadFile" :disabled="!file" title="Download">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
      </button>
    </header>

    <div class="viewer-body">
      <div v-if="loading" class="viewer-status">Loading file...</div>
      <div v-else-if="error" class="viewer-status viewer-error">{{ error }}</div>
      <div v-else-if="binary" class="viewer-status">
        Binary file ({{ formatSize(file.size) }})
        <button class="btn-dl" @click="downloadFile">Download</button>
      </div>
      <pre v-else class="viewer-content"><code>{{ content }}</code></pre>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import * as api from '../api/client.js'

const props = defineProps({
  file: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const content = ref('')
const loading = ref(false)
const error = ref('')
const binary = ref(false)

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0, sz = bytes
  while (sz >= 1024 && i < units.length - 1) { sz /= 1024; i++ }
  return `${sz.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

async function loadFile() {
  if (!props.file) {
    content.value = ''
    binary.value = false
    return
  }
  loading.value = true
  error.value = ''
  binary.value = false
  try {
    const result = await api.readFile(props.file.path)
    if (result?.binary) {
      binary.value = true
      content.value = ''
    } else {
      content.value = result?.content || '(empty)'
    }
  } catch (e) {
    error.value = 'Failed to load file: ' + (e.message || 'Unknown error')
  } finally {
    loading.value = false
  }
}

async function downloadFile() {
  if (!props.file) return
  try {
    const blob = await api.downloadFile(props.file.path)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = props.file.name; document.body.appendChild(a)
    a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
  } catch (e) {
    alert(e.message)
  }
}

watch(() => props.file, loadFile, { immediate: true })
</script>

<style scoped>
.file-viewer {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

.viewer-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.btn-back {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 5px 10px;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-back:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.file-info {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  min-width: 0;
}

.file-name {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
}

.file-path {
  font-size: var(--text-sm);
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.btn-download {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-download:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* Body */
.viewer-body {
  flex: 1;
  overflow: auto;
}

.viewer-body::-webkit-scrollbar {
  width: 6px;
}

.viewer-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

.viewer-content {
  margin: 0;
  padding: 16px 20px;
  font-family: var(--font-mono);
  font-size: var(--text-base);
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
  tab-size: 4;
}

.viewer-status {
  padding: 32px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-base);
}

.viewer-error {
  color: #ef4444;
}

.btn-dl {
  display: inline-block;
  margin-top: 10px;
  padding: 6px 14px;
  background: var(--accent);
  border: none;
  border-radius: var(--radius-sm);
  color: white;
  font-size: var(--text-sm);
  font-family: var(--font-ui);
  cursor: pointer;
}
</style>
