<template>
  <Teleport to="body">
    <div class="toast-container" v-if="toasts.length">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        :class="['toast', `toast-${toast.type}`]"
      >
        <span class="toast-message">{{ toast.message }}</span>
        <button class="toast-close" @click="remove(toast.id)">&times;</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'

const toasts = ref([])
let nextId = 0

function show(message, type = 'info', duration = 4000) {
  const id = nextId++
  toasts.value.push({ id, message, type })
  if (duration > 0) {
    setTimeout(() => remove(id), duration)
  }
}

function remove(id) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function confirm(message) {
  return window.confirm(message)
}

function prompt(message, defaultValue) {
  return window.prompt(message, defaultValue)
}

defineExpose({ show, confirm, prompt })
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 360px;
}

.toast {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: var(--radius-sm);
  font-size: var(--text-base);
  font-weight: 500;
  box-shadow: var(--shadow-md);
  animation: slideIn 0.25s ease;
}

@keyframes slideIn {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

.toast-info {
  background: var(--accent);
  color: white;
}

.toast-error {
  background: #ef4444;
  color: white;
}

.toast-success {
  background: #10b981;
  color: white;
}

.toast-warning {
  background: #f59e0b;
  color: white;
}

.toast-message {
  flex: 1;
}

.toast-close {
  background: none;
  border: none;
  color: inherit;
  opacity: 0.7;
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
  padding: 0;
}

.toast-close:hover {
  opacity: 1;
}
</style>
