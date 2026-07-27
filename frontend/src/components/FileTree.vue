<template>
  <div class="file-tree">
    <div class="tree-header">
      <span class="tree-title">Workspace</span>
      <div class="tree-actions">
        <!-- Upload -->
        <label class="btn-icon upload-btn" title="Upload files">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          <input type="file" hidden multiple @change="handleUpload" ref="fileInput" />
        </label>
        <!-- Download workspace -->
        <button class="btn-icon" title="Download workspace" @click="downloadAll">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
        <!-- New folder -->
        <button class="btn-icon" title="New Folder" @click="newFolder">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
        </button>
        <!-- Refresh -->
        <button class="btn-icon" title="Refresh" @click="loadTree">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
        </button>
      </div>
    </div>

    <div v-if="loading" class="tree-status">Loading...</div>
    <div v-else-if="error" class="tree-status tree-error">{{ error }}</div>
    <div
      v-else
      class="tree-body"
      :class="{ 'drag-over': dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="handleDrop"
    >
      <div v-if="dragOver" class="drop-overlay">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        <span>Drop files to upload</span>
      </div>
      <TreeNode
        v-for="node in treeChildren"
        :key="node.path"
        :node="node"
        :depth="0"
        :selected-path="selectedPath"
        @select="$emit('select', $event)"
        @delete="handleDelete"
        @rename="handleRename"
        @download="handleDownload"
        @open="handleOpen"
      />
      <div v-if="!treeChildren.length" class="tree-status">Empty workspace — drop files or click 📤 to upload</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import * as api from '../api/client.js'
import TreeNode from './TreeNode.vue'

import { useToast } from '../composables/useToast.js'
const toast = useToast()

const props = defineProps({
  selectedPath: { type: String, default: '' }
})
const emit = defineEmits(['select', 'refresh'])

const treeData = ref(null)
const loading = ref(false)
const error = ref('')
const dragOver = ref(false)
const fileInput = ref(null)

const treeChildren = computed(() => treeData.value?.children || [])

async function loadTree() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.fetchTree('', 3)
    treeData.value = data
  } catch (e) {
    console.error('[FileTree] Failed:', e)
    error.value = 'Failed to load files: ' + (e.message || 'Unknown error')
  } finally {
    loading.value = false
  }
}

// ── Upload ──
async function handleUpload(e) {
  const files = e.target.files
  if (!files?.length) return
  try {
    toast.show(`Uploading ${files.length} file(s)...`, 'info', 0)
    await api.uploadFiles(files, '', (done, total) => {
      // update toast or progress
    })
    toast.show(`Uploaded ${files.length} file(s)`, 'success')
    await loadTree()
    emit('refresh')
  } catch (err) {
    toast.error('Upload failed: ' + (err.message || 'Unknown error'))
  }
  // Reset input so same files can be re-selected
  if (fileInput.value) fileInput.value.value = ''
}

// ── Drag & drop ──
let dragCounter = 0
function onDragLeave() {
  dragCounter--
  if (dragCounter <= 0) {
    dragOver.value = false
    dragCounter = 0
  }
}

async function handleDrop(e) {
  dragOver.value = false
  dragCounter = 0
  const files = e.dataTransfer?.files
  if (!files?.length) return
  try {
    toast.show(`Uploading ${files.length} file(s)...`, 'info', 0)
    await api.uploadFiles(files, '', (done, total) => {
      // Could update a progress bar here
    })
    toast.show(`Uploaded ${files.length} file(s)`, 'success')
    await loadTree()
    emit('refresh')
  } catch (err) {
    toast.error('Upload failed: ' + (err.message || 'Unknown error'))
  }
}

// ── Download all ──
async function downloadAll() {
  try {
    const allPaths = treeChildren.value.map(n => n.path)
    if (!allPaths.length) {
      toast.show('Workspace is empty', 'warning')
      return
    }
    const blob = await api.downloadWorkspace(allPaths)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'workspace.zip'; document.body.appendChild(a)
    a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
    toast.show('Downloaded workspace.zip', 'success')
  } catch (err) {
    toast.error('Download failed: ' + (err.message || 'Unknown error'))
  }
}

// ── Single file download (from TreeNode) ──
async function handleDownload(node) {
  try {
    const blob = await api.downloadFile(node.path)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = node.name; document.body.appendChild(a)
    a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
  } catch (err) {
    toast.error('Download failed: ' + (err.message || 'Unknown error'))
  }
}

async function handleOpen(node) {
  try {
    const result = await api.fetchOpenUrl(node.path)
    window.open(result.url, '_blank', 'noopener,noreferrer')
  } catch (err) {
    toast.error('Open failed: ' + (err.message || 'Unknown error'))
  }
}

function handleDelete(node) {
  if (confirm(`Delete "${node.name}"?`)) {
    api.deleteItem(node.path).then(loadTree).catch(e => toast.error(e.message))
  }
}

function handleRename(node) {
  const newName = prompt('New name:', node.name)
  if (newName && newName !== node.name) {
    const parentPath = node.path.split('/').slice(0, -1).join('/')
    const newPath = parentPath ? `${parentPath}/${newName}` : newName
    api.moveItem(node.path, newPath).then(loadTree).catch(e => toast.error(e.message))
  }
}

function newFolder() {
  const name = prompt('Folder name:')
  if (name) {
    const prefix = props.selectedPath || ''
    const fullPath = prefix ? `${prefix}/${name}` : name
    api.createDir(fullPath).then(loadTree).catch(e => toast.error(e.message))
  }
}

onMounted(loadTree)
defineExpose({ loadTree })
</script>

<style scoped>
.file-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-light);
}

.tree-title {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.tree-actions {
  display: flex;
  gap: 2px;
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-icon:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.tree-status {
  padding: 16px;
  font-size: var(--text-sm);
  color: var(--text-tertiary);
  text-align: center;
}

.tree-error {
  color: #ef4444;
}

.tree-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  position: relative;
}

.tree-body.drag-over {
  outline: 2px dashed var(--accent);
  outline-offset: -4px;
  background: var(--accent-light);
}

.drop-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--accent-light);
  color: var(--accent);
  font-size: var(--text-sm);
  font-weight: 600;
  z-index: 10;
  pointer-events: none;
}

.tree-body::-webkit-scrollbar {
  width: 4px;
}

.tree-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 2px;
}

.upload-btn {
  cursor: pointer;
}
.upload-btn input { display: none; }
</style>
