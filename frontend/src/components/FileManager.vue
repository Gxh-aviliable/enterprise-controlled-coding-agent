<template>
  <div class="file-manager">
    <!-- Path Bar -->
    <div class="path-bar">
      <button class="btn-path" @click="navigateTo('')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
        ~
      </button>
      <template v-for="(seg, i) in pathSegments" :key="i">
        <span class="path-sep">/</span>
        <button class="btn-path" @click="navigateTo(pathSegments.slice(0, i + 1).join('/'))">
          {{ seg }}
        </button>
      </template>
      <span class="path-spacer"></span>
      <button class="btn-icon" @click="$emit('refresh')" title="Refresh">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
      </button>
    </div>

    <!-- File Table -->
    <div class="file-table-wrapper">
      <table class="file-table">
        <thead>
          <tr>
            <th class="col-name">Name</th>
            <th class="col-size">Size</th>
            <th class="col-actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="currentPath" class="row-dir" @click="parentDir">
            <td colspan="3">
              <span class="item-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
              </span>
              ..
            </td>
          </tr>
          <tr
            v-for="item in currentItems"
            :key="item.path"
            :class="{ selected: previewFile?.path === item.path }"
            @click="handleItemClick(item)"
          >
            <td class="col-name">
              <span class="item-icon">
                <template v-if="item.type === 'dir'">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
                </template>
                <template v-else>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                </template>
              </span>
              {{ item.name }}
            </td>
            <td class="col-size">{{ item.type === 'dir' ? '—' : formatSize(item.size) }}</td>
            <td class="col-actions">
              <button class="btn-sm" @click.stop="downloadItem(item)" title="Download">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              </button>
              <button class="btn-sm btn-sm-danger" @click.stop="deleteItem(item)" title="Delete">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
              </button>
            </td>
          </tr>
          <tr v-if="!currentItems.length">
            <td colspan="3" class="empty-msg">Empty directory</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Preview -->
    <div class="preview-section">
      <div class="preview-header">
        <span class="preview-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          Preview
          <template v-if="previewFile">: {{ previewFile.name }}</template>
        </span>
      </div>
      <div class="preview-body">
        <div v-if="!previewFile" class="preview-empty">Select a file to preview</div>
        <div v-else-if="previewContent?.binary" class="preview-empty">
          Binary file ({{ formatSize(previewContent.size) }})
          <br />
          <button class="btn-action" @click="downloadItem(previewFile)">Download File</button>
        </div>
        <pre v-else class="preview-code"><code>{{ previewContent?.content || '(empty)' }}</code></pre>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <label class="btn-toolbar btn-upload">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        Upload
        <input type="file" hidden @change="handleUpload" multiple />
      </label>
      <button class="btn-toolbar" @click="newFolder">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>
        New Folder
      </button>
      <span class="toolbar-spacer"></span>
      <button class="btn-toolbar" @click="downloadSelected" :disabled="!selectedCount">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Download {{ selectedCount ? `(${selectedCount})` : '' }}
      </button>
      <button class="btn-toolbar btn-toolbar-danger" @click="deleteSelected" :disabled="!selectedCount">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        Delete {{ selectedCount ? `(${selectedCount})` : '' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import * as api from '../api/client.js'

import { useToast } from '../composables/useToast.js'
const toast = useToast()

const emit = defineEmits(['refresh'])

const currentPath = ref('')
const items = ref([])
const previewFile = ref(null)
const previewContent = ref(null)
const selectedItems = ref(new Set())

const pathSegments = computed(() =>
  currentPath.value ? currentPath.value.split('/').filter(Boolean) : []
)

const currentItems = computed(() =>
  items.value.filter(item => item.type === 'dir').concat(
    items.value.filter(item => item.type === 'file')
  )
)

const selectedCount = computed(() => selectedItems.value.size)

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

async function loadDir(path) {
  try {
    const tree = await api.fetchTree(path || '', 1)
    items.value = tree?.children || []
    items.value.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  } catch (e) {
    console.error('Failed to load directory:', e)
    toast.error('Failed to load directory: ' + (e.message || 'Network error'))
    items.value = []
  }
}

function navigateTo(path) {
  currentPath.value = path
  previewFile.value = null
  previewContent.value = null
  selectedItems.value.clear()
  loadDir(path)
}

function parentDir() {
  if (!currentPath.value) return
  const parent = currentPath.value.split('/').slice(0, -1).join('/')
  navigateTo(parent)
}

function handleItemClick(item) {
  if (item.type === 'dir') {
    navigateTo(item.path)
  } else {
    previewFile.value = item
    loadPreview(item)
  }
}

async function loadPreview(item) {
  try {
    previewContent.value = await api.readFile(item.path)
  } catch (e) {
    previewContent.value = { content: `Error: ${e.message}` }
  }
}

async function downloadItem(item) {
  try {
    const blob = await api.downloadFile(item.path)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = item.name; document.body.appendChild(a)
    a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
  } catch (e) {
    toast.error(e.message)
  }
}

async function deleteItem(item) {
  if (!confirm(`Delete "${item.name}"?`)) return
  try {
    await api.deleteItem(item.path)
    loadDir(currentPath.value)
    if (previewFile.value?.path === item.path) {
      previewFile.value = null
      previewContent.value = null
    }
    emit('refresh')
  } catch (e) { toast.error(e.message) }
}

async function newFolder() {
  const name = prompt('Folder name:')
  if (!name) return
  const fullPath = currentPath.value ? `${currentPath.value}/${name}` : name
  try {
    await api.createDir(fullPath)
    loadDir(currentPath.value)
    emit('refresh')
  } catch (e) { toast.error(e.message) }
}

async function handleUpload(e) {
  const files = e.target.files
  if (!files?.length) return
  try {
    for (const file of files) {
      await api.uploadFile(file, currentPath.value)
    }
    loadDir(currentPath.value)
    emit('refresh')
  } catch (e) { toast.error(e.message) }
}

async function downloadSelected() {
  if (!selectedItems.value.size) return
  try {
    const paths = Array.from(selectedItems.value)
    const blob = await api.downloadZip(paths, 'export')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'export.zip'; document.body.appendChild(a)
    a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
  } catch (e) { toast.error(e.message) }
}

function deleteSelected() {
  if (!selectedItems.value.size) return
  if (!confirm(`Delete ${selectedItems.value.size} item(s)?`)) return
  Promise.all([...selectedItems.value].map(p => api.deleteItem(p).catch(() => {})))
    .then(() => {
      selectedItems.value.clear()
      loadDir(currentPath.value)
      emit('refresh')
    })
}

watch(() => currentPath.value, () => {
  selectedItems.value.clear()
})

loadDir('')
</script>

<style scoped>
.file-manager {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--bg-primary);
}

/* Path Bar */
.path-bar {
  display: flex;
  align-items: center;
  gap: 1px;
  padding: 8px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  flex-shrink: 0;
}

.btn-path {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
  font-family: var(--font-mono);
  padding: 3px 6px;
  border-radius: 4px;
  transition: all var(--transition);
}

.btn-path:hover {
  background: var(--bg-hover);
  color: var(--accent);
}

.path-sep {
  color: var(--border);
  font-size: 14px;
  padding: 0 1px;
}

.path-spacer {
  flex: 1;
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-icon:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* File Table */
.file-table-wrapper {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.file-table-wrapper::-webkit-scrollbar {
  width: 6px;
}

.file-table-wrapper::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

.file-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.file-table th {
  padding: 8px 16px;
  text-align: left;
  color: var(--text-tertiary);
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border-light);
  position: sticky;
  top: 0;
  background: var(--bg-primary);
}

.file-table td {
  padding: 7px 16px;
  border-bottom: 1px solid var(--border-light);
  cursor: pointer;
  transition: background var(--transition);
}

.file-table tbody tr:hover td {
  background: var(--bg-hover);
}

.file-table tr.selected td {
  background: var(--accent-light);
}

.col-name {
  width: auto;
}

.col-size {
  width: 90px;
  color: var(--text-tertiary);
}

.col-actions {
  width: 76px;
  text-align: right;
}

.item-icon {
  margin-right: 6px;
  color: var(--text-tertiary);
  vertical-align: -2px;
}

.row-dir {
  color: var(--accent);
  font-weight: 500;
}

/* Preview */
.preview-section {
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.preview-header {
  padding: 8px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-light);
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.preview-body {
  height: 180px;
  overflow: auto;
  background: var(--bg-secondary);
}

.preview-body::-webkit-scrollbar {
  width: 4px;
}

.preview-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 2px;
}

.preview-code {
  padding: 14px 16px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}

.preview-empty {
  padding: 32px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
}

.btn-action {
  display: inline-block;
  margin-top: 10px;
  padding: 6px 14px;
  background: var(--accent);
  border: none;
  border-radius: var(--radius-sm);
  color: white;
  font-size: 12px;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: background var(--transition);
}

.btn-action:hover {
  background: var(--accent-hover);
}

/* Toolbar */
.toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  background: var(--bg-primary);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.toolbar-spacer {
  flex: 1;
}

.btn-toolbar {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-toolbar:hover:not(:disabled) {
  background: var(--bg-hover);
  border-color: var(--text-tertiary);
}

.btn-toolbar:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-toolbar-danger:hover:not(:disabled) {
  color: #ef4444;
  border-color: #fecaca;
  background: #fef2f2;
}

.btn-upload {
  cursor: pointer;
}

.btn-sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 3px;
  border-radius: 4px;
  transition: all var(--transition);
}

.btn-sm:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.btn-sm-danger:hover {
  color: #ef4444;
  background: #fef2f2;
}

.empty-msg {
  color: var(--text-tertiary);
  text-align: center;
  padding: 32px !important;
  font-size: 13px;
}
</style>
