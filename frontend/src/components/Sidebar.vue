<template>
  <aside class="sidebar">
    <div class="sidebar-brand" @click="$emit('new-session')" title="New Session">
      <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
        <rect width="32" height="32" rx="8" fill="#4f46e5"/>
        <path d="M8 12L16 6L24 12V22C24 23.1046 23.1046 24 22 24H10C8.89543 24 8 23.1046 8 22V12Z" stroke="white" stroke-width="1.5" fill="none"/>
        <circle cx="16" cy="16" r="3" fill="white"/>
      </svg>
      <span class="brand-text">Mini Claude Code</span>
      <ThemeToggle />
    </div>

    <div class="sidebar-tabs">
      <button
        :class="['tab-btn', { active: activeTab === 'sessions' }]"
        @click="activeTab = 'sessions'"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
        </svg>
        <span>Chats</span>
      </button>
      <button
        :class="['tab-btn', { active: activeTab === 'files' }]"
        @click="activeTab = 'files'"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
        </svg>
        <span>Files</span>
      </button>
      <button
        :class="['tab-btn', { active: activeTab === 'memory' }]"
        @click="activeTab = 'memory'"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <span>Memory</span>
      </button>
    </div>

    <!-- Sessions Tab -->
    <div class="tab-content" v-show="activeTab === 'sessions'">
      <div class="section-header">
        <span class="section-title">Conversations</span>
        <button class="btn-new" @click="$emit('new-session')" title="New conversation">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <path d="M12 5v14M5 12h14"/>
          </svg>
        </button>
      </div>

      <div class="session-list">
        <div v-if="!sessions.length" class="empty-state">
          <p>No conversations yet</p>
          <span>Start a new chat to begin</span>
        </div>
        <div
          v-for="s in sessions"
          :key="s.id"
          :class="['session-item', { active: activeId === s.id }]"
          @click="$emit('select', s.id)"
        >
          <div class="session-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
            </svg>
          </div>
          <div class="session-title">{{ s.title || s.id.slice(0, 8) }}</div>
          <button
            class="btn-delete"
            @click.stop="$emit('delete', s.id)"
            title="Delete conversation"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Files Tab -->
    <div class="tab-content file-tab" v-show="activeTab === 'files'">
      <FileTree
        ref="fileTreeRef"
        :selected-path="selectedFilePath"
        @select="handleFileSelect"
      />
    </div>

    <!-- User footer -->
    <div ref="footerRef" class="sidebar-footer" @click="showUserMenu = !showUserMenu">
      <div class="user-info">
        <div class="user-avatar-sm">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        </div>
        <span class="user-name">{{ username }}</span>
      </div>
      <svg :class="['chevron', { rotated: showUserMenu }]" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>

      <!-- Dropdown menu -->
      <div v-if="showUserMenu" class="user-menu" @click.stop>
        <div class="menu-header">
          <span>{{ username }}</span>
        </div>
        <div class="menu-divider"></div>
        <!-- Future items go here -->
        <button class="menu-item" @click="handleLogout">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
          Sign Out
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import FileTree from './FileTree.vue'
import ThemeToggle from './ThemeToggle.vue'
import { auth } from '../stores/auth.js'

defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  selectedFilePath: { type: String, default: '' }
})
const emit = defineEmits(['select', 'delete', 'new-session', 'file-select', 'tab-change'])

// User menu
const showUserMenu = ref(false)
const footerRef = ref(null)

function onDocumentClick(e) {
  if (footerRef.value && !footerRef.value.contains(e.target)) {
    showUserMenu.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))

// Extract username from JWT (base64 decode the payload)
const username = computed(() => {
  const token = localStorage.getItem('access_token')
  if (!token) return 'User'
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.sub || payload.username || 'User'
  } catch { return 'User' }
})

function handleLogout() {
  showUserMenu.value = false
  auth.logout()
}

const activeTab = ref('sessions')
const fileTreeRef = ref(null)

// Auto-refresh file tree + notify App when tab changes
watch(activeTab, async (tab) => {
  emit('tab-change', tab)
  if (tab === 'files') {
    await nextTick()
    fileTreeRef.value?.loadTree()
  }
})

function handleFileSelect(node) {
  emit('file-select', node)
}
</script>

<style scoped>
.sidebar {
  width: 292px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  flex-shrink: 0;
  user-select: none;
}

/* Brand */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 16px 12px;
  cursor: pointer;
  transition: opacity var(--transition);
}

.sidebar-brand:hover {
  opacity: 0.8;
}

.brand-text {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.2px;
}

/* Tabs */
.sidebar-tabs {
  display: flex;
  gap: 2px;
  padding: 0 12px 8px;
  flex-shrink: 0;
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 7px 8px;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition);
}

.tab-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.tab-btn.active {
  color: var(--text-primary);
  background: var(--bg-primary);
  box-shadow: var(--shadow-sm);
}

.tab-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.file-tab {
  padding: 0;
}

/* Section header */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
}

.section-title {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.btn-new {
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

.btn-new:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* Session list */
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}

.session-list::-webkit-scrollbar {
  width: 4px;
}

.session-list::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 2px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition);
  position: relative;
  margin-bottom: 1px;
}

.session-item:hover {
  background: var(--bg-hover);
}

.session-item.active {
  background: var(--bg-active);
}

.session-item.active .session-title {
  color: var(--accent);
  font-weight: 500;
}

.session-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
  display: flex;
  margin-top: 1px;
}

.session-item.active .session-icon {
  color: var(--accent);
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: 400;
}

.btn-delete {
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 2px;
  border-radius: 3px;
  transition: all var(--transition);
}

.session-item:hover .btn-delete {
  opacity: 1;
}

.btn-delete:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.08);
}

/* Empty */
.empty-state {
  padding: 32px 16px;
  text-align: center;
}

.empty-state p {
  color: var(--text-secondary);
  font-size: var(--text-base);
  font-weight: 500;
  margin: 0 0 4px;
}

.empty-state span {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

/* User footer */
.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-top: 1px solid var(--border-light);
  flex-shrink: 0;
  cursor: pointer;
  position: relative;
  transition: background var(--transition);
}

.sidebar-footer:hover {
  background: var(--bg-hover);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.user-avatar-sm {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  background: var(--accent-light);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-name {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s ease;
}

.chevron.rotated {
  transform: rotate(180deg);
}

/* Dropdown menu */
.user-menu {
  position: absolute;
  bottom: 100%;
  left: 8px;
  right: 8px;
  margin-bottom: 4px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: 200;
  overflow: hidden;
}

.menu-header {
  padding: 10px 14px 6px;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.menu-divider {
  height: 1px;
  background: var(--border-light);
  margin: 4px 0;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 14px;
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-family: var(--font-ui);
  cursor: pointer;
  transition: background var(--transition);
  text-align: left;
}

.menu-item:hover {
  background: var(--bg-hover);
}

.menu-item:last-child:hover {
  background: #fef2f2;
  color: #ef4444;
}
</style>
