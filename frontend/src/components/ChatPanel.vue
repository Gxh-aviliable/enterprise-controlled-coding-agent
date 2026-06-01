<template>
  <div class="chat-panel">
    <!-- Header -->
    <header class="chat-header">
      <div class="header-left">
        <span class="session-badge">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
          </svg>
          {{ activeId ? activeId.slice(0, 8) : 'New conversation' }}
        </span>
        <span v-if="streaming" class="status-badge streaming">
          <span class="pulse-dot"></span>
          Generating
        </span>
        <span v-if="currentTool && !streaming" class="status-badge tool">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
          {{ currentTool }}
        </span>
        <span v-if="pendingConfirm" class="status-badge awaiting">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          Awaiting confirmation
        </span>
      </div>
      <div class="header-right">
        <button class="btn-header" @click="$emit('session-created', '')" title="New conversation">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <path d="M12 5v14M5 12h14"/>
          </svg>
        </button>
      </div>
    </header>

    <!-- Messages -->
    <div class="messages" ref="msgContainer">
      <div v-if="messages.length === 0 && !streaming" class="welcome">
        <div class="welcome-icon">
          <svg width="48" height="48" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="#4f46e5" opacity="0.12"/>
            <path d="M8 12L16 6L24 12V22C24 23.1046 23.1046 24 22 24H10C8.89543 24 8 23.1046 8 22V12Z" stroke="#4f46e5" stroke-width="1.5" fill="none"/>
            <circle cx="16" cy="16" r="3" fill="#4f46e5"/>
          </svg>
        </div>
        <h2>Mini Claude Code</h2>
        <p>Ask anything — code, analysis, file operations, and more.</p>
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
        :class="['message-wrapper', msg.role]"
      >
        <div class="message-avatar">
          <template v-if="msg.role === 'user'">
            <div class="avatar user-avatar">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            </div>
          </template>
          <template v-else>
            <div class="avatar agent-avatar">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
            </div>
          </template>
        </div>
        <div class="message-body">
          <div class="message-role">{{ msg.role === 'user' ? 'You' : 'Claude' }}</div>
          <div :class="['message-content', { streaming: msg.streaming }]">
            <div
              v-if="msg.role === 'assistant'"
              class="message-text markdown-body"
              v-html="renderMarkdown(msg.content)"
            ></div>
            <pre v-else class="message-text">{{ msg.content }}</pre>
          </div>
        </div>
      </div>

      <div v-if="streaming && messages.length === 0" class="message-wrapper assistant">
        <div class="message-avatar">
          <div class="avatar agent-avatar">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
          </div>
        </div>
        <div class="message-body">
          <div class="message-role">Claude</div>
          <div class="message-content streaming">
            <span class="thinking-dots">Thinking<span class="dots"></span></span>
          </div>
        </div>
      </div>
    </div>

    <!-- Tool Confirmation Modal -->
    <Teleport to="body">
      <div v-if="pendingConfirm" class="modal-overlay" @click.self="rejectTools">
        <div class="modal-card">
          <div class="modal-header">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            <h3>Confirm Tool Execution</h3>
          </div>
          <p class="modal-message">{{ pendingConfirm.message }}</p>
          <ul class="modal-tools">
            <li v-for="(tool, idx) in pendingConfirm.tools" :key="tool.id || idx" class="modal-tool-item">
              <label class="tool-label">
                <input type="checkbox" v-model="tool.approved" />
                <div class="tool-info">
                  <strong>{{ tool.name }}</strong>
                  <span>{{ tool.description }}</span>
                </div>
              </label>
            </li>
          </ul>
          <div class="modal-actions">
            <button @click="rejectTools" class="btn-modal btn-reject">Reject All</button>
            <button @click="approveAll" class="btn-modal btn-approve-all">Approve All</button>
            <button @click="approveTools" class="btn-modal btn-approve">Approve Selected</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Input Area -->
    <div class="input-area">
      <div class="input-wrapper">
        <textarea
          ref="inputEl"
          v-model="input"
          @keydown.enter.exact="send"
          placeholder="Send a message... (Enter to send, Shift+Enter for new line)"
          :disabled="streaming || pendingConfirm"
          rows="1"
          @input="autoResize"
        ></textarea>
        <button
          @click="send"
          :disabled="streaming || pendingConfirm || !input.trim()"
          class="btn-send"
          title="Send message"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="19" x2="12" y2="5"/>
            <polyline points="5 12 12 5 19 12"/>
          </svg>
        </button>
      </div>
      <p class="input-hint">
        Mini Claude Code — Enterprise Agent Console
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import * as api from '../api/client.js'

import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true
})

function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'u', 's', 'del', 'code', 'pre',
      'ul', 'ol', 'li', 'a', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'img', 'span'],
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'target', 'rel']
  })
}

const props = defineProps({
  sessionId: { type: String, default: '' }
})
const emit = defineEmits(['session-created'])

const input = ref('')
const messages = ref([])
const streaming = ref(false)
const currentTool = ref('')
const msgContainer = ref(null)
const inputEl = ref(null)
const activeId = ref(props.sessionId)
const pendingConfirm = ref(null)
const streamMsgRef = ref(null)
const abortController = ref(null)

function getAbortSignal() {
  // Abort any existing stream before starting a new one
  if (abortController.value) {
    abortController.value.abort()
  }
  abortController.value = new AbortController()
  return abortController.value.signal
}

function scrollBottom() {
  nextTick(() => {
    const el = msgContainer.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function autoResize() {
  nextTick(() => {
    const el = inputEl.value
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 160) + 'px'
    }
  })
}

function extractTitle(content) {
  // Take the first sentence (up to 。.!！?？\n) or first 50 chars, whichever is shorter
  const sentenceMatch = content.match(/^(.+?)[。.!！?？\n]/)
  if (sentenceMatch) return sentenceMatch[1].slice(0, 50)
  return content.slice(0, 50)
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value || pendingConfirm.value) return
  input.value = ''
  nextTick(() => {
    if (inputEl.value) {
      inputEl.value.style.height = 'auto'
    }
  })

  messages.value.push({ role: 'user', content })
  scrollBottom()

  let sid = activeId.value
  const isNewSession = !sid
  if (isNewSession) {
    try {
      const title = extractTitle(content)
      const session = await api.createSession(title)
      sid = session.id
      activeId.value = sid
    } catch (e) {
      messages.value.push({ role: 'assistant', content: 'Failed to create session: ' + e.message })
      return
    }
  }

  const streamMsg = { role: 'assistant', content: '', streaming: true }
  messages.value.push(streamMsg)
  streamMsgRef.value = streamMsg
  streaming.value = true
  scrollBottom()

  const signal = getAbortSignal()
  startStream(sid, content, isNewSession, signal)
}

function startStream(sessionId, content, isNewSession, signal) {
  api.streamMessage({
    session_id: sessionId,
    signal,
    content,
    onDelta: (delta) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += delta
        scrollBottom()
      }
    },
    onToolStart: (name) => {
      currentTool.value = name
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n🔧 *Running \`${name}\`...*`
        scrollBottom()
      }
    },
    onToolEnd: (name) => {
      currentTool.value = ''
    },
    onInterrupt: (data) => {
      const tools = (data.tools || []).map(t => ({ ...t, approved: true }))
      pendingConfirm.value = {
        session_id: sessionId,
        message: data.message || 'Confirm tool execution?',
        tools,
        isNewSession
      }
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) {
        streamMsgRef.value.content += '\n\n---\n⏳ *Awaiting your confirmation...*'
        streamMsgRef.value.streaming = false
        scrollBottom()
      }
    },
    onError: (err) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n❌ **Error:** ${err}`
      }
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
    },
    onDone: () => {
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
      if (isNewSession && !pendingConfirm.value) {
        emit('session-created', sessionId)
      }
      scrollBottom()
    }
  })
}

function approveTools() {
  if (!pendingConfirm.value) return
  const approvedIds = pendingConfirm.value.tools.filter(t => t.approved).map(t => t.id)
  resumeAfterConfirm(approvedIds)
}

function approveAll() {
  if (!pendingConfirm.value) return
  const allIds = pendingConfirm.value.tools.map(t => t.id)
  resumeAfterConfirm(allIds)
}

function rejectTools() {
  if (!pendingConfirm.value) return
  if (streamMsgRef.value) {
    streamMsgRef.value.content += '\n\n❌ *Tool execution rejected by user*'
    scrollBottom()
  }
  if (pendingConfirm.value.isNewSession) {
    emit('session-created', pendingConfirm.value.session_id)
  }
  pendingConfirm.value = null
  streamMsgRef.value = null
}

function resumeAfterConfirm(approvedIds) {
  if (!pendingConfirm.value) return
  const sessionId = pendingConfirm.value.session_id
  const isNewSession = pendingConfirm.value.isNewSession
  pendingConfirm.value = null
  streaming.value = true
  currentTool.value = ''

  if (streamMsgRef.value) {
    streamMsgRef.value.content += `\n\n✅ *Proceeding with ${approvedIds.length} approved tool(s)...*`
    streamMsgRef.value.streaming = true
    scrollBottom()
  }

  const signal = getAbortSignal()
  api.resumeStream({
    session_id: sessionId,
    approved: true,
    approved_ids: approvedIds,
    signal,
    onDelta: (delta) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += delta
        scrollBottom()
      }
    },
    onToolStart: (name) => {
      currentTool.value = name
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n🔧 *Running \`${name}\`...*`
        scrollBottom()
      }
    },
    onToolEnd: (name) => { currentTool.value = '' },
    onInterrupt: (data) => {
      const tools = (data.tools || []).map(t => ({ ...t, approved: true }))
      pendingConfirm.value = {
        session_id: sessionId,
        message: data.message || 'Confirm tool execution?',
        tools,
        isNewSession
      }
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) {
        streamMsgRef.value.content += '\n\n---\n⏳ *Awaiting your confirmation...*'
        streamMsgRef.value.streaming = false
        scrollBottom()
      }
    },
    onError: (err) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n❌ **Error:** ${err}`
      }
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
    },
    onDone: () => {
      streaming.value = false
      currentTool.value = ''
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
      if (isNewSession) emit('session-created', sessionId)
      scrollBottom()
    }
  })
}

watch(() => props.sessionId, async (newId) => {
  if (newId !== activeId.value) {
    // Abort any in-progress SSE stream to prevent memory leak
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    activeId.value = newId
    streaming.value = false
    currentTool.value = ''
    pendingConfirm.value = null
    streamMsgRef.value = null

    // Load existing messages from backend, or start fresh
    if (newId) {
      try {
        const data = await api.getSessionMessages(newId)
        messages.value = (data.messages || []).map(m => ({
          role: m.role,
          content: m.content,
          streaming: false
        }))
        scrollBottom()
      } catch (e) {
        console.error('Failed to load session messages:', e)
        messages.value = []
      }
    } else {
      messages.value = []
    }
  }
})
</script>

<style scoped>
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  position: relative;
  min-height: 0;
}

/* Header */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
  background: var(--bg-primary);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.session-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--text-secondary);
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  font-weight: 500;
  padding: 3px 10px;
  border-radius: 20px;
}

.status-badge.streaming {
  color: #d97706;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #d97706;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

.status-badge.tool {
  color: #7c3aed;
  background: #f5f3ff;
  border: 1px solid #ddd6fe;
}

.status-badge.awaiting {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
}

.btn-header {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-header:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* Messages */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
  display: flex;
  flex-direction: column;
}

.messages::-webkit-scrollbar {
  width: 6px;
}

.messages::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

.messages::-webkit-scrollbar-thumb:hover {
  background: var(--text-tertiary);
}

/* Welcome */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: 40px;
  text-align: center;
}

.welcome-icon {
  margin-bottom: 16px;
  opacity: 0.7;
}

.welcome h2 {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 6px;
  letter-spacing: -0.3px;
}

.welcome p {
  font-size: var(--text-base);
  color: var(--text-tertiary);
  margin: 0;
}

/* Message row */
.message-wrapper {
  display: flex;
  gap: 12px;
  padding: 16px 28px;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
  transition: background 0.2s;
}

.message-wrapper.user {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
  margin-top: 4px;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-avatar {
  background: var(--accent);
  color: white;
}

.agent-avatar {
  background: var(--accent-light);
  color: var(--accent);
}

.message-body {
  flex: 1;
  min-width: 0;
}

.message-role {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 4px;
  padding: 0 2px;
}

.message-wrapper.user .message-role {
  text-align: right;
}

.message-content {
  border-radius: var(--radius-md);
  padding: 14px 18px;
}

.message-wrapper.user .message-content {
  background: var(--accent);
}

.message-wrapper.assistant .message-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
}

.message-wrapper.user .message-content .message-text {
  color: white;
}

.message-wrapper.assistant .message-content .message-text {
  color: var(--text-primary);
}

.message-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-ui);
  font-size: var(--text-md);
  line-height: 1.7;
}

/* Markdown rendered content */
.markdown-body {
  white-space: normal;
}

.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

.markdown-body :deep(em) {
  font-style: italic;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px;
}

.markdown-body :deep(li) {
  margin-bottom: 2px;
}

.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: var(--text-base);
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
}

.message-wrapper.user .markdown-body :deep(code) {
  background: rgba(255, 255, 255, 0.15);
  color: white;
}

.markdown-body :deep(pre) {
  margin: 8px 0;
  padding: 12px 14px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: var(--radius-sm);
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: var(--text-base);
  line-height: 1.5;
}

.markdown-body :deep(pre code) {
  background: none;
  padding: 0;
  font-size: inherit;
}

.markdown-body :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--accent-soft);
  color: var(--text-secondary);
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 10px 0 6px;
  font-weight: 600;
  line-height: 1.3;
}

.markdown-body :deep(h1) { font-size: 18px; }
.markdown-body :deep(h2) { font-size: 16px; }
.markdown-body :deep(h3) { font-size: 15px; }
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 10px 0;
}

.markdown-body :deep(a) {
  color: var(--accent);
  text-decoration: underline;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
  font-size: var(--text-base);
}

.markdown-body :deep(th) {
  background: var(--bg-tertiary);
  font-weight: 600;
}

.message-text.streaming::after {
  content: '';
  display: inline-block;
  width: 8px;
  height: 16px;
  background: var(--accent);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 0.8s infinite;
  border-radius: 1px;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* Thinking dots */
.thinking-dots {
  color: var(--text-tertiary);
  font-size: var(--text-base);
  font-style: italic;
}

.dots::after {
  content: '';
  animation: dots 1.5s steps(4, end) infinite;
}

@keyframes dots {
  0% { content: ''; }
  25% { content: '.'; }
  50% { content: '..'; }
  75% { content: '...'; }
  100% { content: ''; }
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  max-width: 460px;
  width: 92vw;
  box-shadow: var(--shadow-lg);
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.modal-header h3 {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.modal-message {
  color: var(--text-secondary);
  font-size: var(--text-base);
  margin: 0 0 16px;
  padding-left: 30px;
}

.modal-tools {
  list-style: none;
  padding: 0;
  margin: 0 0 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.modal-tool-item {
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-light);
  transition: all var(--transition);
}

.modal-tool-item:hover {
  border-color: var(--accent-soft);
}

.tool-label {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  cursor: pointer;
}

.tool-label input[type="checkbox"] {
  margin-top: 3px;
  accent-color: var(--accent);
}

.tool-info {
  flex: 1;
}

.tool-info strong {
  color: var(--accent);
  font-size: var(--text-base);
  font-weight: 600;
}

.tool-info span {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  display: block;
  margin-top: 3px;
}

.modal-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.btn-modal {
  padding: 8px 16px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: var(--text-base);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-reject {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.btn-reject:hover {
  background: #fef2f2;
  color: #ef4444;
}

.btn-approve-all {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.btn-approve-all:hover {
  background: var(--bg-hover);
}

.btn-approve {
  background: var(--accent);
  color: white;
}

.btn-approve:hover {
  background: var(--accent-hover);
}

/* Input */
.input-area {
  padding: 12px 24px 16px;
  flex-shrink: 0;
  background: linear-gradient(to top, var(--bg-primary) 80%, transparent);
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  max-width: 772px;
  margin: 0 auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 8px 8px 8px 16px;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.input-wrapper:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.input-wrapper textarea {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-family: var(--font-ui);
  line-height: 1.5;
  outline: none;
  resize: none;
  padding: 4px 0;
  max-height: 160px;
}

.input-wrapper textarea::placeholder {
  color: var(--text-tertiary);
}

.input-wrapper textarea:disabled {
  opacity: 0.5;
}

.btn-send {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  background: var(--accent);
  border: none;
  border-radius: var(--radius-md);
  color: white;
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--transition);
}

.btn-send:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: translateY(-1px);
}

.btn-send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.input-hint {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin: 8px 0 0;
}
</style>
