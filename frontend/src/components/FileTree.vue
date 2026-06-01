<template>
  <div class="file-tree">
    <div class="tree-header">
      <span class="tree-title">Workspace</span>
      <div class="tree-actions">
        <button class="btn-icon" title="Refresh" @click="loadTree">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
        </button>
        <button class="btn-icon" title="New Folder" @click="newFolder">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
        </button>
      </div>
    </div>

    <div v-if="loading" class="tree-status">Loading...</div>
    <div v-else-if="error" class="tree-status tree-error">{{ error }}</div>
    <div v-else class="tree-body">
      <TreeNode
        v-for="node in treeChildren"
        :key="node.path"
        :node="node"
        :depth="0"
        :selected-path="selectedPath"
        @select="$emit('select', $event)"
        @delete="handleDelete"
        @rename="handleRename"
      />
      <div v-if="!treeChildren.length" class="tree-status">Empty workspace</div>
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

const treeChildren = computed(() => treeData.value?.children || [])

async function loadTree() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.fetchTree('', 3)
    console.log('[FileTree] Loaded tree:', data)
    treeData.value = data
  } catch (e) {
    console.error('[FileTree] Failed:', e)
    error.value = 'Failed to load files: ' + (e.message || 'Unknown error')
  } finally {
    loading.value = false
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
}

.tree-body::-webkit-scrollbar {
  width: 4px;
}

.tree-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 2px;
}
</style>
