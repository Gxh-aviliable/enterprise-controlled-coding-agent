<template>
  <LoginForm v-if="!auth.loggedIn" />

  <div v-else class="app-layout">
    <Sidebar
      :sessions="sessions"
      :activeId="activeSessionId"
      :selectedFilePath="selectedFile?.path"
      @select="selectSession"
      @new-session="newSession"
      @delete="deleteSession"
      @file-select="onFileSelect"
    />

    <div class="main-area">
      <ChatPanel
        v-if="!selectedFile"
        :sessionId="activeSessionId"
        @session-created="onSessionCreated"
      />
      <FileViewer
        v-else
        :file="selectedFile"
        @close="selectedFile = null"
      />
    </div>
  </div>
  <Toast />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { auth } from './stores/auth.js'
import * as api from './api/client.js'
import LoginForm from './components/LoginForm.vue'
import Sidebar from './components/Sidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import FileViewer from './components/FileViewer.vue'
import Toast from './components/Toast.vue'

const sessions = ref([])
const activeSessionId = ref('')
const selectedFile = ref(null)

async function loadSessions() {
  try {
    const data = await api.listSessions()
    sessions.value = Array.isArray(data) ? data : data.sessions || []
  } catch (e) {
    console.error('Failed to load sessions:', e)
    sessions.value = []
  }
}

function selectSession(id) {
  activeSessionId.value = id
  selectedFile.value = null
}

function newSession() {
  activeSessionId.value = ''
  selectedFile.value = null
}

async function onSessionCreated(sid) {
  activeSessionId.value = sid
  await loadSessions()
}

function onFileSelect(node) {
  if (node.type === 'file') {
    selectedFile.value = node
  }
}

async function deleteSession(id) {
  try {
    await api.deleteSession(id)
    if (activeSessionId.value === id) activeSessionId.value = ''
    await loadSessions()
  } catch (e) {
    console.error('Failed to delete session:', e)
  }
}

onMounted(() => {
  if (auth.loggedIn) loadSessions()
})
</script>

<style>
:root {
  /* DeepSeek-inspired palette */
  --bg-primary: #ffffff;
  --bg-secondary: #f8f9fb;
  --bg-tertiary: #f3f4f6;
  --bg-hover: #f0f1f3;
  --bg-active: #ecedf6;
  --text-primary: #1a1a2e;
  --text-secondary: #6b7280;
  --text-tertiary: #9ca3af;
  --text-inverse: #ffffff;
  --accent: #4f46e5;
  --accent-hover: #4338ca;
  --accent-light: #eef2ff;
  --accent-soft: #e0e7ff;
  --border: #e5e7eb;
  --border-light: #f3f4f6;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.06);
  --shadow-lg: 0 12px 40px rgba(0, 0, 0, 0.08);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  --radius-xl: 24px;
  --font-ui: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono: 'JetBrains Mono', 'Consolas', 'Monaco', monospace;
  --transition: 150ms cubic-bezier(0.4, 0, 0.2, 1);

  /* Font size scale (base = 16px) */
  --text-xs: 0.75rem;   /* 12px — hints, badges */
  --text-sm: 0.8125rem; /* 13px — secondary labels */
  --text-base: 0.9375rem; /* 15px — body, tree nodes, session items */
  --text-md: 1rem;      /* 16px — message text, inputs */
  --text-lg: 1.125rem;  /* 18px — headings */
  --text-xl: 1.25rem;   /* 20px — title */
  --text-2xl: 1.5rem;   /* 24px — brand, welcome */
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: var(--font-ui);
  font-size: 16px;
  line-height: 1.5;
  background: var(--bg-primary);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-primary);
}

</style>
