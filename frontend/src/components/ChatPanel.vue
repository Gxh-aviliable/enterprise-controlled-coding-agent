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
    <div class="messages-region">
    <div class="messages" ref="msgContainer" @scroll.passive="handleMessagesScroll">
      <div v-if="messages.length === 0 && !streaming" class="welcome">
        <div class="welcome-icon">
          <svg width="48" height="48" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="#4f46e5" opacity="0.12"/>
            <path d="M8 12L16 6L24 12V22C24 23.1046 23.1046 24 22 24H10C8.89543 24 8 23.1046 8 22V12Z" stroke="#4f46e5" stroke-width="1.5" fill="none"/>
            <circle cx="16" cy="16" r="3" fill="#4f46e5"/>
          </svg>
        </div>
        <template v-if="activeId && emptyHistory">
          <h2>No saved messages</h2>
          <p>This conversation was created, but no chat history was saved.</p>
        </template>
        <template v-else>
          <h2>Mini Claude Code</h2>
          <p>Ask anything — code, analysis, file operations, and more.</p>
        </template>
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
      >
        <ToolCallCard
          v-if="msg.role === 'tool_call'"
          :name="msg.toolName"
          :status="msg.toolStatus"
          :result="msg.toolResult"
          :error="msg.toolError"
          :duration="msg.toolDuration"
        />
        <div
          v-else
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
      <button
        v-if="!autoFollow && messages.length > 0"
        type="button"
        class="jump-to-latest"
        aria-label="Jump to latest message"
        @click="resumeAutoFollow"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M6 13l6 6 6-6"/></svg>
        Latest
      </button>
    </div>

    <!-- Explicit mode escalation: never silently simulate Multi-Agent in Single mode. -->
    <Teleport to="body">
      <div v-if="pendingModeRequest" class="modal-overlay" @click.self="cancelModeSwitch">
        <div class="modal-card mode-confirm-card">
          <div class="modal-header">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/></svg>
            <h3>Use real Multi-Agent execution?</h3>
          </div>
          <p class="modal-message">
            This prompt explicitly asks agents to collaborate. Single mode will not create Python
            scripts or role-play to imitate multiple agents.
          </p>
          <div class="modal-actions">
            <button @click="cancelModeSwitch" class="btn-modal btn-reject">Cancel</button>
            <button @click="confirmModeSwitch" class="btn-modal btn-approve">
              Switch to Multi and send
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Tool Confirmation Modal -->
    <Teleport to="body">
      <div v-if="pendingConfirm" class="modal-overlay" @click.self="rejectTools">
        <div class="modal-card">
          <div class="modal-header">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            <h3>Confirm Tool Execution</h3>
          </div>
          <p class="modal-message">{{ pendingConfirm.message }}</p>
          <p class="modal-scope-note">
            Safe shell commands run automatically. Approval applies only to this current batch.
          </p>
          <ul class="modal-tools">
            <li v-for="(tool, idx) in pendingConfirm.tools" :key="tool.id || idx" class="modal-tool-item">
              <label class="tool-label">
                <input type="checkbox" v-model="tool.approved" />
                <div class="tool-info">
                  <div class="tool-title-row">
                    <strong>{{ tool.name }}</strong>
                    <span :class="['risk-badge', `risk-${tool.risk || 'review'}`]">
                      {{ tool.risk || 'review' }}
                    </span>
                  </div>
                  <span>{{ tool.description }}</span>
                </div>
              </label>
            </li>
          </ul>
          <div class="modal-actions">
            <button @click="rejectTools" class="btn-modal btn-reject">Reject All</button>
            <button @click="approveAll" class="btn-modal btn-approve-all">Approve Current Batch</button>
            <button @click="approveTools" class="btn-modal btn-approve">Approve Selected</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Input Area -->
    <div class="input-area">
      <div class="execution-mode-bar">
        <div class="execution-mode-copy">
          <strong>Execution mode</strong>
          <span v-if="executionMode === 'multi_agent'">Real specialist delegation is required and traced.</span>
          <span v-else>Reliable single-Agent baseline; collaboration is never simulated.</span>
        </div>
        <div class="mode-switch" aria-label="Agent execution mode" data-testid="execution-mode-switch">
          <button
            type="button"
            :class="['mode-option', { active: executionMode === 'single_agent' }]"
            :disabled="streaming || pendingConfirm"
            @click="executionMode = 'single_agent'"
          >
            Single
          </button>
          <button
            type="button"
            :class="['mode-option', { active: executionMode === 'multi_agent' }]"
            :disabled="streaming || pendingConfirm || !multiAgentAvailable"
            :title="multiAgentAvailable ? 'Use real specialist subagents' : multiAgentReason"
            @click="executionMode = 'multi_agent'"
          >
            Multi <span class="experimental-label">EXP</span>
          </button>
        </div>
      </div>
      <div class="input-wrapper">
        <textarea
          ref="inputEl"
          v-model="input"
          @keydown.enter.exact="send"
          placeholder="Send a message... (Enter to send, Shift+Enter for new line)"
          :disabled="streaming || !!pendingConfirm || !!pendingModeRequest"
          rows="1"
          @input="autoResize"
        ></textarea>
        <!-- Stop button (shown during generation, hidden during tool confirmation) -->
        <button
          v-if="streaming && !pendingConfirm"
          @click="stopGeneration"
          class="btn-send btn-stop"
          title="Stop generation"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="1.5"/>
          </svg>
        </button>
        <!-- Send button (shown when idle or during tool confirmation) -->
        <button
          v-else
          @click="send"
          :disabled="streaming || pendingConfirm || pendingModeRequest || !input.trim()"
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
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import * as api from '../api/client.js'
import ToolCallCard from './ToolCallCard.vue'

import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from '../utils/highlight.js'
import 'highlight.js/styles/github.css'

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

let _highlightTimer = null
function scheduleHighlight() {
  // Debounce: highlight after DOM settles (good for streaming)
  clearTimeout(_highlightTimer)
  _highlightTimer = setTimeout(() => {
    nextTick(() => {
      document.querySelectorAll('.markdown-body pre code').forEach(el => {
        hljs.highlightElement(el)
      })
    })
  }, 120)
}

const props = defineProps({
  sessionId: { type: String, default: '' }
})
const emit = defineEmits(['session-created'])

const input = ref('')
const messages = ref([])
const streaming = ref(false)
const currentTool = ref('')
const emptyHistory = ref(false)
const autoFollow = ref(true)
const msgContainer = ref(null)
const inputEl = ref(null)
const activeId = ref(props.sessionId)
const pendingConfirm = ref(null)
const pendingModeRequest = ref('')
const streamMsgRef = ref(null)
const abortController = ref(null)
const executionMode = ref('single_agent')
const agentCapabilities = ref(null)
const multiAgentAvailable = computed(() =>
  agentCapabilities.value?.available_modes?.includes('multi_agent') === true
)
const multiAgentReason = computed(() =>
  agentCapabilities.value?.multi_agent_reason || 'Multi-Agent capability is unavailable.'
)
let historyLoadRequestId = 0

const multiAgentTerms = [
  '多智能体', '多代理', '多个智能体', '多个代理', '子智能体', '子代理',
  'multi-agent', 'multi agent', 'multiple agents', 'subagent', 'sub-agent'
]
const multiAgentActions = [
  '协作', '合作', '分工', '委派', '调用', '使用', '运用', '让',
  'delegate', 'collaborat', 'work together', 'use'
]

function requestsMultiAgentExecution(content) {
  const normalized = content.trim().toLowerCase().replace(/\s+/g, ' ')
  return multiAgentTerms.some(term => normalized.includes(term)) &&
    multiAgentActions.some(action => normalized.includes(action))
}

function getAbortSignal() {
  // Abort any existing stream before starting a new one
  if (abortController.value) {
    abortController.value.abort()
  }
  abortController.value = new AbortController()
  return abortController.value.signal
}

async function stopGeneration() {
  const sid = activeId.value
  if (!sid) return

  // 1. Abort the SSE fetch immediately (stops receiving tokens)
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }

  // 2. Preserve partial content in the streaming message
  if (streamMsgRef.value) {
    streamMsgRef.value.streaming = false
    if (streamMsgRef.value.content) {
      streamMsgRef.value.content += '\n\n*[Generation stopped by user]*'
    } else {
      streamMsgRef.value.content = '*[Generation stopped before any response was received]*'
    }
  }

  // 3. Reset streaming state
  streaming.value = false
  currentTool.value = ''
  streamMsgRef.value = null

  // 4. Mark any unresolved tool cards as cancelled
  for (const m of messages.value) {
    if (m.role === 'tool_call' && ['running', 'waiting'].includes(m.toolStatus)) {
      m.toolStatus = 'error'
      m.toolError = 'Stopped by user'
    }
  }

  // 5. Send cancel request to backend (fire-and-forget)
  try {
    await api.cancelStream(sid)
  } catch (e) {
    console.warn('[stop] Backend cancel request failed (non-fatal):', e)
  }

  scrollBottom()
}

const AUTO_FOLLOW_THRESHOLD_PX = 80

function isNearBottom(element) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= AUTO_FOLLOW_THRESHOLD_PX
}

function handleMessagesScroll() {
  const element = msgContainer.value
  if (element) autoFollow.value = isNearBottom(element)
}

function scrollBottom(force = false) {
  nextTick(() => {
    const el = msgContainer.value
    if (!el || (!force && !autoFollow.value)) return
    el.scrollTop = el.scrollHeight
    autoFollow.value = true
  })
}

function resumeAutoFollow() {
  autoFollow.value = true
  scrollBottom(true)
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

  if (executionMode.value === 'single_agent' && requestsMultiAgentExecution(content)) {
    if (multiAgentAvailable.value) {
      pendingModeRequest.value = content
    } else {
      messages.value.push({
        role: 'assistant',
        content: `❌ **Multi-Agent unavailable:** ${multiAgentReason.value}`
      })
      scrollBottom()
    }
    return
  }

  await submitContent(content)
}

function cancelModeSwitch() {
  pendingModeRequest.value = ''
}

async function confirmModeSwitch() {
  const content = pendingModeRequest.value
  if (!content) return
  pendingModeRequest.value = ''
  executionMode.value = 'multi_agent'
  await submitContent(content)
}

async function submitContent(content) {
  input.value = ''
  nextTick(() => {
    if (inputEl.value) {
      inputEl.value.style.height = 'auto'
    }
  })

  messages.value.push({ role: 'user', content })
  emptyHistory.value = false
  scrollBottom(true)

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
  scrollBottom(true)

  const signal = getAbortSignal()
  startStream(sid, content, isNewSession, signal)
}

function findToolMessage(id, name, statuses = ['running', 'waiting']) {
  if (id) {
    const exact = messages.value.findLast(
      m => m.role === 'tool_call' && m.toolCallId === id && statuses.includes(m.toolStatus)
    )
    if (exact) return exact
  }
  return messages.value.findLast(
    m => m.role === 'tool_call' && (!name || m.toolName === name) && statuses.includes(m.toolStatus)
  )
}

function startToolCard(name, id) {
  if (id) {
    const existing = messages.value.findLast(
      m => m.role === 'tool_call' && m.toolCallId === id && ['running', 'waiting'].includes(m.toolStatus)
    )
    if (existing) return existing
  }
  const toolMsg = {
    role: 'tool_call',
    toolCallId: id || '',
    toolName: name,
    toolStatus: 'running',
    toolResult: '',
    toolError: '',
    toolDuration: null,
    _startTime: Date.now()
  }
  messages.value.push(toolMsg)
  return toolMsg
}

function finishToolCard(name, metadata = {}) {
  const toolMsg = findToolMessage(metadata.id, name)
  if (!toolMsg) return
  toolMsg.toolDuration = metadata.duration_ms ?? (Date.now() - toolMsg._startTime)
  if (metadata.ok === true || metadata.status === 'success') {
    toolMsg.toolStatus = 'done'
    toolMsg.toolError = ''
  } else {
    toolMsg.toolStatus = 'error'
    toolMsg.toolError = metadata.error_code
      ? `Tool failed: ${metadata.error_code}`
      : 'Tool execution failed'
  }
}

function applyToolResult(id, result, metadata = {}) {
  const toolMsg = findToolMessage(id, metadata.name)
  if (!toolMsg) return
  if (result !== undefined && result !== null) {
    toolMsg.toolResult = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
  }
}

function failUnresolvedTools(reason) {
  for (const message of messages.value) {
    if (message.role === 'tool_call' && ['running', 'waiting'].includes(message.toolStatus)) {
      message.toolStatus = 'error'
      message.toolError = reason
      message.toolDuration ??= Date.now() - message._startTime
    }
  }
}

function markConfirmationWaiting(data) {
  for (const tool of data.tools || []) {
    const toolMsg = findToolMessage(tool.id, tool.name, ['running'])
    if (toolMsg) toolMsg.toolStatus = 'waiting'
  }
}

function startStream(sessionId, content, isNewSession, signal) {
  api.streamMessage({
    session_id: sessionId,
    signal,
    content,
    mode: executionMode.value,
    onDelta: (delta) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += delta
        scheduleHighlight()
        scrollBottom()
      }
    },
    onToolStart: (name, id) => {
      currentTool.value = name
      startToolCard(name, id)
      scrollBottom()
    },
    onToolEnd: (name, metadata) => {
      currentTool.value = ''
      finishToolCard(name, metadata)
    },
    onToolResult: (id, result, metadata) => {
      applyToolResult(id, result, metadata)
    },
    onInterrupt: (data) => {
      markConfirmationWaiting(data)
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
        streamMsgRef.value.streaming = false
      }
    },
    onError: (err) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n❌ **Error:** ${err}`
      }
      streaming.value = false
      currentTool.value = ''
      failUnresolvedTools(`Stream failed: ${err}`)
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
    },
    onDone: () => {
      streaming.value = false
      currentTool.value = ''
      failUnresolvedTools('Stream ended before an authoritative tool result was received')
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
  resumeAfterConfirm(approvedIds, true)
}

function approveAll() {
  if (!pendingConfirm.value) return
  const allIds = pendingConfirm.value.tools.map(t => t.id)
  resumeAfterConfirm(allIds, true)
}

function rejectTools() {
  if (!pendingConfirm.value) return
  resumeAfterConfirm([], false)
}

function resumeAfterConfirm(approvedIds, approved) {
  if (!pendingConfirm.value) return
  const confirmation = pendingConfirm.value
  const sessionId = confirmation.session_id
  const isNewSession = confirmation.isNewSession
  const sensitiveIds = new Set(confirmation.tools.map(tool => tool.id))
  const approvedSet = new Set(approvedIds)
  for (const message of messages.value) {
    if (message.role !== 'tool_call' || !['running', 'waiting'].includes(message.toolStatus)) continue
    if (!approved || (sensitiveIds.has(message.toolCallId) && !approvedSet.has(message.toolCallId))) {
      message.toolStatus = 'error'
      message.toolError = 'Tool execution was not approved'
    } else if (approvedSet.has(message.toolCallId)) {
      message.toolStatus = 'running'
    }
  }
  pendingConfirm.value = null
  streaming.value = true
  currentTool.value = ''

  if (streamMsgRef.value) {
    streamMsgRef.value.streaming = true
  }

  const signal = getAbortSignal()
  api.resumeStream({
    session_id: sessionId,
    approved,
    approved_ids: approvedIds,
    signal,
    onDelta: (delta) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += delta
        scheduleHighlight()
        scrollBottom()
      }
    },
    onToolStart: (name, id) => {
      currentTool.value = name
      startToolCard(name, id)
      scrollBottom()
    },
    onToolEnd: (name, metadata) => {
      currentTool.value = ''
      finishToolCard(name, metadata)
    },
    onToolResult: (id, result, metadata) => {
      applyToolResult(id, result, metadata)
    },
    onInterrupt: (data) => {
      markConfirmationWaiting(data)
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
        streamMsgRef.value.streaming = false
      }
    },
    onError: (err) => {
      if (streamMsgRef.value) {
        streamMsgRef.value.content += `\n\n❌ **Error:** ${err}`
      }
      streaming.value = false
      currentTool.value = ''
      failUnresolvedTools(`Stream failed: ${err}`)
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
    },
    onDone: () => {
      streaming.value = false
      currentTool.value = ''
      failUnresolvedTools('Stream ended before an authoritative tool result was received')
      if (streamMsgRef.value) streamMsgRef.value.streaming = false
      if (isNewSession) emit('session-created', sessionId)
      scrollBottom()
    }
  })
}

async function loadSessionMessages(sessionId) {
  const requestId = ++historyLoadRequestId

  if (!sessionId) {
    messages.value = []
    emptyHistory.value = false
    autoFollow.value = true
    return
  }

  try {
    const data = await api.getSessionMessages(sessionId)
    // Ignore a response that arrived after a session switch or component teardown.
    if (requestId !== historyLoadRequestId || activeId.value !== sessionId) return

    messages.value = (data.messages || []).map(m => ({
      role: m.role,
      content: m.content,
      streaming: false
    }))
    emptyHistory.value = messages.value.length === 0 && data.message_count === 0
    scheduleHighlight()
    scrollBottom(true)
  } catch (e) {
    if (requestId !== historyLoadRequestId || activeId.value !== sessionId) return

    console.error('Failed to load session messages:', e)
    messages.value = []
    emptyHistory.value = true
  }
}

watch(() => props.sessionId, async (newId) => {
  if (newId !== activeId.value) {
    // Abort any in-progress SSE stream to prevent memory leak
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    // Cancel the backend stream for the old session (fire-and-forget)
    const oldId = activeId.value
    if (oldId) {
      api.cancelStream(oldId).catch(() => {})
    }
    activeId.value = newId
    streaming.value = false
    currentTool.value = ''
    pendingConfirm.value = null
    pendingModeRequest.value = ''
    streamMsgRef.value = null
    emptyHistory.value = false
    autoFollow.value = true

    // Load existing messages from backend, or start fresh.
    await loadSessionMessages(newId)
  }
})

// Best-effort cancellation on tab close / refresh.
// Uses sendBeacon because the browser may cancel in-flight fetch requests
// during page unload. Not all browsers send Authorization headers with
// sendBeacon, so this is best-effort — the 24h TTL on Redis handles
// cleanup if the beacon fails.
const _handleBeforeUnload = () => {
  if (activeId.value && streaming.value) {
    const BASE = import.meta.env.VITE_API_BASE || '/api'
    // sendBeacon with no body — session_id is in the query param
    navigator.sendBeacon(
      `${BASE}/chat/stream/cancel?session_id=${encodeURIComponent(activeId.value)}`
    )
  }
}

onMounted(() => {
  window.addEventListener('beforeunload', _handleBeforeUnload)
  api.getAgentCapabilities()
    .then(data => {
      agentCapabilities.value = data
      if (!data.available_modes?.includes(executionMode.value)) {
        executionMode.value = 'single_agent'
      }
    })
    .catch(error => {
      console.warn('[capabilities] Failed to load Agent capabilities:', error)
      executionMode.value = 'single_agent'
    })
  // A component can be recreated with an already-selected session (for example
  // after a parent remount). In that case the prop watcher does not fire.
  if (activeId.value) {
    loadSessionMessages(activeId.value)
  }
})

onUnmounted(() => {
  // Invalidate any history response that may still be in flight.
  historyLoadRequestId += 1
  window.removeEventListener('beforeunload', _handleBeforeUnload)
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

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode-switch {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  border: 1px solid var(--border-light);
  border-radius: 9px;
  background: var(--bg-secondary);
}

.mode-option {
  border: 0;
  border-radius: 6px;
  padding: 5px 9px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
}

.mode-option.active {
  background: var(--bg-primary);
  color: var(--accent);
  box-shadow: 0 1px 3px rgb(15 23 42 / 10%);
}

.mode-option:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.experimental-label {
  margin-left: 2px;
  font-size: 8px;
  letter-spacing: 0.04em;
  color: var(--accent);
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

/* Stop button — same shape as send, red background */
.btn-send.btn-stop {
  background: #ef4444;
  color: white;
}

.btn-send.btn-stop:hover {
  background: #dc2626;
  transform: translateY(-1px);
}

/* Messages */
.messages-region {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
}

.messages {
  flex: 1;
  min-height: 0;
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

.jump-to-latest {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  box-shadow: var(--shadow-md);
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition: color var(--transition), border-color var(--transition), transform var(--transition);
}

.jump-to-latest:hover {
  color: var(--accent);
  border-color: var(--accent);
  transform: translateX(-50%) translateY(-1px);
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

.modal-scope-note {
  margin: -8px 0 14px;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
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

.tool-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
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

.risk-badge {
  display: inline-flex !important;
  margin-top: 0 !important;
  padding: 2px 6px;
  border-radius: 999px;
  font-size: 9px !important;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.risk-review {
  background: #fff7ed;
  color: #c2410c !important;
}

.risk-dangerous {
  background: #fef2f2;
  color: #dc2626 !important;
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

.execution-mode-bar {
  max-width: 772px;
  margin: 0 auto 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 10px 8px 12px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
}

.execution-mode-copy {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.execution-mode-copy strong {
  flex-shrink: 0;
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.execution-mode-copy span {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mode-confirm-card {
  max-width: 520px;
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

@media (max-width: 760px) {
  .execution-mode-copy span {
    display: none;
  }

  .input-area {
    padding-left: 12px;
    padding-right: 12px;
  }
}
</style>
