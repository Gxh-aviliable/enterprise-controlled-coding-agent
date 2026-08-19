const BASE = '/api'

function getToken() {
  return localStorage.getItem('access_token')
}

function getRefreshToken() {
  return localStorage.getItem('refresh_token')
}

function setTokens(access, refresh) {
  localStorage.setItem('access_token', access)
  if (refresh) localStorage.setItem('refresh_token', refresh)
}

function errorMessage(detail, fallback) {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return detail.message
  }
  return fallback
}

export function clearTokens() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}

// Refresh lock — prevents concurrent 401s from triggering multiple refresh calls
let _refreshPromise = null

async function _tryRefreshToken() {
  if (_refreshPromise) return _refreshPromise

  const refresh = getRefreshToken()
  if (!refresh) return null

  _refreshPromise = (async () => {
    try {
      const refreshRes = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh })
      })
      if (refreshRes.ok) {
        const data = await refreshRes.json()
        setTokens(data.access_token, data.refresh_token)
        return data.access_token
      }
      // Refresh failed — clear tokens so caller can redirect to login
      clearTokens()
      return null
    } finally {
      _refreshPromise = null
    }
  })()

  return _refreshPromise
}

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...options.headers }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let res = await fetch(`${BASE}${path}`, { ...options, headers })

  // Auto-refresh on 401 — uses shared lock to prevent token refresh storms
  if (res.status === 401) {
    const newToken = await _tryRefreshToken()
    if (newToken) {
      headers['Authorization'] = `Bearer ${newToken}`
      res = await fetch(`${BASE}${path}`, { ...options, headers })
    }
  }
  return res
}

// Auth API
export async function register({ username, email, password, full_name }) {
  const res = await request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, email, password, full_name })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Register failed')
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function login({ email, password }) {
  const res = await request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Login failed')
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function getMe() {
  const res = await request('/auth/me')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load user profile')
  return data
}

export async function forgotPassword({ email }) {
  const res = await request('/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to request reset code')
  return data
}

export async function resetPassword({ email, code, new_password }) {
  const res = await request('/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ email, code, new_password })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to reset password')
  return data
}

// Chat API
export async function getAgentCapabilities() {
  const res = await request('/chat/capabilities')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load Agent capabilities')
  return data
}

export async function sendMessage({ session_id, content, mode = 'single_agent' }) {
  const res = await request('/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ session_id, content, stream: false, mode })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(errorMessage(data.detail, 'Chat failed'))
  return data
}

async function consumeEventStream(res, handlers = {}, label = 'stream') {
  const {
    onDelta,
    onToolStart,
    onToolEnd,
    onToolResult,
    onTaskStarted,
    onCancelled,
    onInterrupt,
    onError,
    onDone
  } = handlers
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handlePayload = (payload) => {
    if (payload === '[DONE]') {
      onDone?.()
      return true
    }

    let data
    try {
      data = JSON.parse(payload)
    } catch (error) {
      console.warn(`[${label}] Failed to parse SSE data:`, payload, error)
      return false
    }

    if (data.delta !== undefined) {
      onDelta?.(data.delta, data)
    } else if (data.event === 'task_started') {
      onTaskStarted?.(data)
    } else if (data.event === 'tool_start') {
      onToolStart?.(data.name, data.id, data)
    } else if (data.event === 'tool_end') {
      onToolEnd?.(data.name, data)
    } else if (data.event === 'tool_result') {
      onToolResult?.(data.id, data.result, data)
    } else if (data.event === 'cancelled') {
      onCancelled?.(data)
      return true
    } else if (data.event === 'interrupt') {
      const interruptData = { ...(data.data || {}) }
      if (data.session_id && !interruptData.session_id) interruptData.session_id = data.session_id
      if (data.trace_id && !interruptData.trace_id) interruptData.trace_id = data.trace_id
      onInterrupt?.(interruptData)
      return true
    } else if (data.error) {
      onError?.(data.error)
      return true
    }
    return false
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      if (handlePayload(line.slice(6))) return
    }
  }

  const trailing = buffer.trim()
  if (trailing.startsWith('data: ')) {
    if (handlePayload(trailing.slice(6))) return
  }
  onError?.('Stream transport closed before a terminal event was received.')
}

async function openEventStream(path, options, handlers, fallbackError, label) {
  try {
    const res = await request(path, options)
    if (!res.ok) {
      let errText = fallbackError
      try {
        const err = await res.json()
        errText = errorMessage(err.detail, errText)
      } catch {}
      handlers.onError?.(errText)
      return
    }
    await consumeEventStream(res, handlers, label)
  } catch (error) {
    if (error.name === 'AbortError') return
    console.error(`[${label}] Fetch error:`, error)
    handlers.onError?.(error.message)
  }
}

export function streamMessage({
  session_id,
  content,
  mode = 'single_agent',
  signal,
  ...handlers
}) {
  return openEventStream(
    '/chat/stream',
    {
      method: 'POST',
      signal,
      body: JSON.stringify({ session_id, content, stream: true, mode })
    },
    handlers,
    'Stream failed',
    'stream'
  )
}

// Resume stream after an existing tool-confirmation interrupt.
export function resumeStream({
  session_id,
  trace_id,
  approved,
  approved_ids,
  signal,
  ...handlers
}) {
  const params = new URLSearchParams({
    session_id,
    approved: String(approved)
  })
  return openEventStream(
    `/chat/stream/resume?${params}`,
    {
      method: 'POST',
      signal,
      body: JSON.stringify({ approved_ids: approved_ids || [], trace_id: trace_id || null })
    },
    handlers,
    'Resume failed',
    'resume-confirmation'
  )
}

export async function getStreamStatus(sessionId) {
  const params = new URLSearchParams({ session_id: sessionId })
  const res = await request(`/chat/stream/status?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(errorMessage(data.detail, 'Failed to load stream status'))
  return data
}

// Session API
export async function listSessions() {
  const res = await request('/sessions/')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to list sessions')
  return data
}

export async function createSession(title) {
  const res = await request('/sessions/', {
    method: 'POST',
    body: JSON.stringify({ title })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to create session')
  return data
}

export async function deleteSession(sessionId) {
  const res = await request(`/sessions/${sessionId}`, { method: 'DELETE' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to delete session')
  return data
}

export async function getSessionMessages(sessionId) {
  const res = await request(`/sessions/${sessionId}/messages`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load messages')
  return data
}

// Task trace API
export async function listTaskRuns(limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) })
  const res = await request(`/tasks?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to list task traces')
  return data
}

export async function getTaskMetrics() {
  const res = await request('/tasks/metrics')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load task metrics')
  return data
}

export async function replayTaskTrace(traceId) {
  const res = await request(`/tasks/${encodeURIComponent(traceId)}/trace`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to replay task trace')
  return data
}

// Stream cancellation
export async function cancelStream(sessionId, traceId = '') {
  const params = new URLSearchParams({ session_id: sessionId })
  if (traceId) params.set('trace_id', traceId)
  const res = await request(
    `/chat/stream/cancel?${params}`,
    { method: 'POST' }
  )
  const data = await res.json()
  if (!res.ok) throw new Error(errorMessage(data.detail, 'Cancel failed'))
  return data
}

// Workspace API

export async function fetchTree(path = '', depth = 2) {
  const params = new URLSearchParams({ path, depth: String(depth) })
  const res = await request(`/workspace/tree?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to fetch tree')
  return data
}

export async function readFile(path, offset = 0, limit = 500) {
  const params = new URLSearchParams({ path, offset: String(offset), limit: String(limit) })
  const res = await request(`/workspace/read?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to read file')
  return data
}

export async function saveFile(path, content, expectedSha256) {
  const res = await request('/workspace/write', {
    method: 'PUT',
    body: JSON.stringify({
      path,
      content,
      expected_sha256: expectedSha256
    })
  })
  const data = await res.json()
  if (!res.ok) {
    const detail = data.detail
    const error = new Error(errorMessage(detail, 'Failed to save file'))
    error.status = res.status
    if (detail && typeof detail === 'object') {
      error.code = detail.code
      error.currentSha256 = detail.current_sha256
    }
    throw error
  }
  return data
}

export async function downloadFile(path) {
  const token = getToken()
  const params = new URLSearchParams({ path })
  const res = await fetch(`${BASE}/workspace/download?${params}`, {
    headers: { Authorization: `Bearer ${token}` }
  })
  if (!res.ok) throw new Error('Download failed')
  return res.blob()
}

export async function fetchOpenUrl(path) {
  const params = new URLSearchParams({ path })
  const res = await request(`/workspace/open-url?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to build open URL')
  return data
}

export async function downloadZip(paths, name = 'workspace') {
  const token = getToken()
  const params = new URLSearchParams({ paths: paths.join(','), name })
  const res = await fetch(`${BASE}/workspace/download-zip?${params}`, {
    headers: { Authorization: `Bearer ${token}` }
  })
  if (!res.ok) throw new Error('Download failed')
  return res.blob()
}

export async function uploadFile(file, path = '') {
  const token = getToken()
  const formData = new FormData()
  formData.append('file', file)
  const params = new URLSearchParams({ path })
  const res = await fetch(`${BASE}/workspace/upload?${params}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Upload failed')
  return data
}

export async function uploadFiles(files, path = '', onProgress) {
  let done = 0
  const results = []
  for (const file of files) {
    const result = await uploadFile(file, path)
    results.push(result)
    done++
    onProgress?.(done, files.length, result)
  }
  return results
}

export async function downloadWorkspace(files) {
  if (!files || !files.length) {
    throw new Error('No files to download')
  }
  const paths = files.map(f => f.path || f)
  return downloadZip(paths, 'workspace')
}

export async function createDir(path) {
  const params = new URLSearchParams({ path })
  const res = await request(`/workspace/mkdir?${params}`, { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to create directory')
  return data
}

// Memory API
export async function fetchMemories(limit = 50, minImportance = 0) {
  const params = new URLSearchParams({ limit: String(limit), min_importance: String(minImportance) })
  const res = await request(`/memory/conversations?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to fetch memories')
  return data
}

export async function fetchPatterns() {
  const res = await request('/memory/patterns')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to fetch patterns')
  return data
}

export async function deleteMemory(docId) {
  const res = await request(`/memory/conversations/${encodeURIComponent(docId)}`, { method: 'DELETE' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Failed to delete memory')
  }
  return res.json()
}

export async function deletePattern(patternId) {
  const res = await request(`/memory/patterns/${encodeURIComponent(patternId)}`, { method: 'DELETE' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Failed to delete pattern')
  }
  return res.json()
}

export async function deleteItem(path) {
  const params = new URLSearchParams({ path })
  const res = await request(`/workspace/delete?${params}`, { method: 'DELETE' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to delete')
  return data
}

export async function moveItem(from, to) {
  const params = new URLSearchParams()
  params.append('from', from)
  params.append('to', to)
  const res = await request(`/workspace/move?${params}`, { method: 'PUT' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to move')
  return data
}

// Admin control-room API
async function adminJson(path, options = {}) {
  const res = await request(path, options)
  const data = await res.json()
  if (!res.ok) {
    throw new Error(errorMessage(data.detail, 'Administrator action failed'))
  }
  return data
}

export async function getAdminOverview() {
  const res = await request('/admin/overview')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load admin overview')
  return data
}

export async function listAdminUsers({ q = '', active = '', page = 1, limit = 25 } = {}) {
  const params = new URLSearchParams({ q, page: String(page), limit: String(limit) })
  if (active !== '') params.set('active', String(active))
  const res = await request(`/admin/users?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load users')
  return data
}

export async function getAdminUser(userId) {
  const res = await request(`/admin/users/${userId}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load user')
  return data
}

export async function revokeAdminUserSessions(userId, reason) {
  return adminJson(`/admin/users/${userId}/revoke-sessions`, {
    method: 'POST',
    body: JSON.stringify({ reason })
  })
}

export async function updateAdminUserStatus(userId, isActive, reason) {
  const res = await request(`/admin/users/${userId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: isActive, reason })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to update user status')
  return data
}

export async function updateAdminUserQuota(userId, quota) {
  const res = await request(`/admin/users/${userId}/quota`, {
    method: 'PATCH',
    body: JSON.stringify(quota)
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to update quota')
  return data
}

export async function getAdminWorkspaceTree(userId, path = '', depth = 2) {
  const params = new URLSearchParams({ path, depth: String(depth) })
  const res = await request(`/admin/users/${userId}/workspace/tree?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load workspace metadata')
  return data
}

export async function createAdminAccessGrant(targetUserId, reason, ttlMinutes = 10) {
  const res = await request('/admin/access-grants', {
    method: 'POST',
    body: JSON.stringify({
      target_user_id: targetUserId,
      scope: 'workspace:content',
      reason,
      ttl_minutes: ttlMinutes
    })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to create temporary access grant')
  return data
}

export async function readAdminWorkspaceFile(userId, path, grantId, offset = 0, limit = 500) {
  const params = new URLSearchParams({
    path,
    grant_id: grantId,
    offset: String(offset),
    limit: String(limit)
  })
  const res = await request(`/admin/users/${userId}/workspace/read?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to read guarded workspace content')
  return data
}

export async function listAdminSkills() {
  const res = await request('/admin/skills')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load Shared Skills')
  return data
}

export async function getAdminSkill(name) {
  const res = await request(`/admin/skills/${encodeURIComponent(name)}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load Shared Skill')
  return data
}

export async function saveAdminSkillDraft(skill) {
  const res = await request('/admin/skills', {
    method: 'POST',
    body: JSON.stringify(skill)
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to save Shared Skill draft')
  return data
}

export async function validateAdminSkill(name) {
  const res = await request(`/admin/skills/${encodeURIComponent(name)}/validate`, { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail?.message || data.detail || 'Skill validation failed')
  return data
}

export async function publishAdminSkill(name, changelog, expectedUpdatedAt = null) {
  const res = await request(`/admin/skills/${encodeURIComponent(name)}/publish`, {
    method: 'POST',
    body: JSON.stringify({ changelog, expected_updated_at: expectedUpdatedAt })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail?.message || data.detail || 'Failed to publish Shared Skill')
  return data
}

export async function retireAdminSkill(name, reason) {
  const params = new URLSearchParams({ reason })
  const res = await request(`/admin/skills/${encodeURIComponent(name)}/retire?${params}`, { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to retire Shared Skill')
  return data
}

export async function rollbackAdminSkill(name, version, reason) {
  return adminJson(`/admin/skills/${encodeURIComponent(name)}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ version, reason })
  })
}

export async function cancelAdminTask(traceId, reason) {
  return adminJson(`/admin/tasks/${encodeURIComponent(traceId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ reason })
  })
}

export async function listAdminAuditLogs(limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) })
  const res = await request(`/admin/audit-logs?${params}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load audit logs')
  return data
}

export async function getAdminSystemHealth() {
  const res = await request('/admin/system/health')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to load system health')
  return data
}
