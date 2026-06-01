<template>
  <div class="tree-row-wrapper">
    <div
      :class="['tree-row', { selected: selectedPath === node.path }]"
      :style="{ paddingLeft: (depth * 20 + 14) + 'px' }"
      @click="$emit('select', node)"
      @contextmenu.prevent="showMenu = !showMenu"
    >
      <span class="node-toggle" @click.stop="expanded = !expanded" v-if="node.type === 'dir'">
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          :class="{ rotated: expanded }"
        >
          <path d="M9 18l6-6-6-6"/>
        </svg>
      </span>
      <span class="node-toggle node-toggle-placeholder" v-else></span>

      <span class="node-icon">
        <template v-if="node.type === 'dir' && expanded">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2v3" opacity="0.6"/><path d="M2 13h2l1 3h14l1-3h2v7a2 2 0 01-2 2H4a2 2 0 01-2-2v-7z"/></svg>
        </template>
        <template v-else-if="node.type === 'dir'">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
        </template>
        <template v-else>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        </template>
      </span>
      <span class="node-name">{{ node.name }}</span>

      <div v-if="showMenu" class="ctx-menu" @click.stop>
        <button v-if="node.type === 'file'" @click="emit('download', node); showMenu = false">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download
        </button>
        <button @click="emit('rename', node); showMenu = false">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
          Rename
        </button>
        <button class="ctx-danger" @click="emit('delete', node); showMenu = false">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
          Delete
        </button>
      </div>
    </div>

    <!-- Children are OUTSIDE the flex row, each on its own line -->
    <template v-if="expanded && node.type === 'dir' && node.children">
      <TreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :selected-path="selectedPath"
        @select="$emit('select', $event)"
        @delete="$emit('delete', $event)"
        @rename="$emit('rename', $event)"
        @download="$emit('download', $event)"
      />
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  selectedPath: { type: String, default: '' }
})
const emit = defineEmits(['select', 'delete', 'rename', 'download'])

const expanded = ref(false)
const showMenu = ref(false)
</script>

<style scoped>
.tree-row-wrapper {
  /* Each tree level is a block — children below the row */
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 10px 4px 0;
  cursor: pointer;
  font-size: var(--text-base);
  color: var(--text-primary);
  position: relative;
  user-select: none;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  margin-right: 8px;
  transition: background var(--transition);
}

.tree-row:hover {
  background: var(--bg-hover);
}

.tree-row.selected {
  background: var(--accent-light);
  color: var(--accent);
}

.node-toggle {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--text-tertiary);
  border-radius: 3px;
  transition: all var(--transition);
}

.node-toggle:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.node-toggle svg {
  transition: transform 0.15s ease;
}

.node-toggle svg.rotated {
  transform: rotate(90deg);
}

.node-toggle-placeholder {
  visibility: hidden;
}

.node-icon {
  flex-shrink: 0;
  display: flex;
  color: var(--text-tertiary);
  margin-right: 4px;
}

.tree-row.selected .node-icon {
  color: var(--accent);
}

.node-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 400;
}

.tree-row.selected .node-name {
  font-weight: 500;
}

/* Context menu */
.ctx-menu {
  position: absolute;
  right: 4px;
  top: 100%;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px;
  z-index: 100;
  box-shadow: var(--shadow-md);
  display: flex;
  flex-direction: column;
  min-width: 120px;
}

.ctx-menu button {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: none;
  color: var(--text-primary);
  padding: 6px 10px;
  font-size: var(--text-base);
  font-family: var(--font-ui);
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
  white-space: nowrap;
  transition: all var(--transition);
}

.ctx-menu button:hover {
  background: var(--bg-hover);
}

.ctx-menu .ctx-danger:hover {
  background: #fef2f2;
  color: #ef4444;
}
</style>
