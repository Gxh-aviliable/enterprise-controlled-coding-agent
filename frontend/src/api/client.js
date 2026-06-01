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

export async function login({ username, password }) {
  const res = await request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Login failed')
  setTokens(data.access_token, data.refresh_token)
  return data
}

// Chat API
export async function sendMessage({ session_id, content }) {
  const res = await request('/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ session_id, content, stream: false })
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Chat failed')
  return data
}

export function streamMessage({ session_id, content, signal, onDelta, onToolStart, onToolEnd, onToolResult, onInterrupt, onError, onDone }) {
  const token = getToken()
  console.log('[stream] Starting stream, session:', session_id, 'content:', content.slice(0, 50))

  fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ session_id, content, stream: true })
  }).then(async (res) => {
    console.log('[stream] Response status:', res.status)

    if (!res.ok) {
      let errText = 'Stream failed'
      try {
        const err = await res.json()
        errText = err.detail || errText
      } catch {}
      console.error('[stream] HTTP error:', errText)
      onError(errText)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let streamHadContent = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        console.log('[stream] Stream ended, had content:', streamHadContent)
        break
      }
      const chunk = decoder.decode(value, { stream: true })
      buffer += chunk
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const payload = line.slice(6)
          if (payload === '[DONE]') {
            console.log('[stream] Received [DONE]')
            onDone()
            return
          }
          try {
            const json = JSON.parse(payload)
            if (json.delta !== undefined) {
              streamHadContent = true
              onDelta(json.delta)
            } else if (json.event === 'tool_start') {
              console.log('[stream] Tool start:', json.name)
              onToolStart(json.name)
            } else if (json.event === 'tool_end') {
              console.log('[stream] Tool end:', json.name)
              onToolEnd(json.name)
            } else if (json.event === 'tool_result') {
              onToolResult?.(json.id, json.result)
            } else if (json.event === 'interrupt') {
              // Interrupt received - tool confirmation required
              console.log('[stream] Interrupt received:', json.data)
              onInterrupt(json.data)
              // Stop current stream, wait for user confirmation
              return
            } else if (json.error) {
              console.error('[stream] Error event:', json.error)
              onError(json.error)
            }
          } catch (e) {
            console.warn('[stream] Failed to parse SSE data:', payload, e)
          }
        }
      }
    }
    // If we exit the loop without [DONE], still call onDone
    if (!streamHadContent) {
      console.warn('[stream] Stream ended without any content')
    }
    onDone()
  }).catch(err => {
    console.error('[stream] Fetch error:', err)
    onError(err.message)
  })
}

// Resume stream after interrupt confirmation
export function resumeStream({ session_id, approved, approved_ids, signal, onDelta, onToolStart, onToolEnd, onToolResult, onInterrupt, onError, onDone }) {
  const token = getToken()
  console.log('[resume] Resuming stream, session:', session_id, 'approved:', approved)

  // Build URL with query params
  let url = `${BASE}/chat/stream/resume?session_id=${encodeURIComponent(session_id)}&approved=${approved}`

  fetch(url, {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ approved_ids: approved_ids || [] })
  }).then(async (res) => {
    console.log('[resume] Response status:', res.status)

    if (!res.ok) {
      let errText = 'Resume failed'
      try {
        const err = await res.json()
        errText = err.detail || errText
      } catch {}
      console.error('[resume] HTTP error:', errText)
      onError(errText)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        console.log('[resume] Stream ended')
        break
      }
      const chunk = decoder.decode(value, { stream: true })
      buffer += chunk
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const payload = line.slice(6)
          if (payload === '[DONE]') {
            console.log('[resume] Received [DONE]')
            onDone()
            return
          }
          try {
            const json = JSON.parse(payload)
            if (json.delta !== undefined) {
              onDelta(json.delta)
            } else if (json.event === 'tool_start') {
              onToolStart(json.name)
            } else if (json.event === 'tool_end') {
              onToolEnd(json.name)
            } else if (json.event === 'interrupt') {
              // Another interrupt (multiple tool confirmations)
              console.log('[resume] Another interrupt:', json.data)
              onInterrupt(json.data)
              return
            } else if (json.error) {
              onError(json.error)
            }
          } catch (e) {
            console.warn('[resume] Failed to parse SSE data:', payload)
          }
        }
      }
    }
    onDone()
  }).catch(err => {
    console.error('[resume] Fetch error:', err)
    onError(err.message)
  })
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

export async function downloadFile(path) {
  const token = getToken()
  const params = new URLSearchParams({ path })
  const res = await fetch(`${BASE}/workspace/download?${params}`, {
    headers: { Authorization: `Bearer ${token}` }
  })
  if (!res.ok) throw new Error('Download failed')
  return res.blob()
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
