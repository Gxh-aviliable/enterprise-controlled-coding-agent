import { afterEach, describe, expect, it, vi } from 'vitest'

import { cancelStream, saveFile, sendMessage, streamMessage } from '../src/api/client.js'

function failedResponse(message) {
  return {
    ok: false,
    status: 429,
    json: vi.fn().mockResolvedValue({
      detail: {
        code: 'quota_exceeded',
        message
      }
    })
  }
}

function streamResponse(...events) {
  const encoder = new TextEncoder()
  const chunks = events.map(event => encoder.encode(
    `data: ${typeof event === 'string' ? event : JSON.stringify(event)}\n\n`
  ))
  let index = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: vi.fn(async () => index < chunks.length
          ? { done: false, value: chunks[index++] }
          : { done: true, value: undefined })
      })
    }
  }
}

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(data)
  }
}

describe('API client structured errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('shows the quota message for non-streaming chat requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failedResponse('Daily task quota exceeded')))

    await expect(sendMessage({ session_id: 'session-1', content: 'run task' }))
      .rejects.toThrow('Daily task quota exceeded')
  })

  it('shows the quota message for streaming chat requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failedResponse('Concurrent task quota exceeded')))
    const onError = vi.fn()

    streamMessage({
      session_id: 'session-1',
      content: 'run task',
      onError,
      onDelta: vi.fn(),
      onToolStart: vi.fn(),
      onToolEnd: vi.fn(),
      onDone: vi.fn()
    })

    await vi.waitFor(() => {
      expect(onError).toHaveBeenCalledWith('Concurrent task quota exceeded')
    })
  })
})

describe('API client task-control events', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('routes task_started and paused without treating pause as completion or HITL', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse(
      { event: 'task_started', session_id: 'session-1', trace_id: 'trace-1', status: 'pending' },
      { event: 'paused', session_id: 'session-1', trace_id: 'trace-1', status: 'paused' }
    )))
    const onTaskStarted = vi.fn()
    const onPaused = vi.fn()
    const onInterrupt = vi.fn()
    const onDone = vi.fn()

    await streamMessage({
      session_id: 'session-1',
      content: 'pause me safely',
      onTaskStarted,
      onPaused,
      onInterrupt,
      onDone
    })

    expect(onTaskStarted).toHaveBeenCalledWith(expect.objectContaining({ trace_id: 'trace-1' }))
    expect(onPaused).toHaveBeenCalledWith(expect.objectContaining({
      trace_id: 'trace-1', status: 'paused'
    }))
    expect(onInterrupt).not.toHaveBeenCalled()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('routes cancelled as a terminal control event without reporting normal completion', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse(
      { event: 'cancelled', session_id: 'session-1', trace_id: 'trace-2', message: 'Cancelled' }
    )))
    const onCancelled = vi.fn()
    const onDone = vi.fn()

    await streamMessage({
      session_id: 'session-1',
      content: 'cancel me',
      onCancelled,
      onDone
    })

    expect(onCancelled).toHaveBeenCalledWith(expect.objectContaining({ trace_id: 'trace-2' }))
    expect(onDone).not.toHaveBeenCalled()
  })

  it('routes a typed user_pause interrupt to onPaused, not tool confirmation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse({
      event: 'interrupt',
      data: { type: 'user_pause', trace_id: 'trace-3', resume_target: 'tool_executor' }
    })))
    const onPaused = vi.fn()
    const onInterrupt = vi.fn()

    await streamMessage({
      session_id: 'session-1',
      content: 'pause',
      onPaused,
      onInterrupt
    })

    expect(onPaused).toHaveBeenCalledWith(expect.objectContaining({
      event: 'paused', type: 'user_pause', trace_id: 'trace-3'
    }))
    expect(onInterrupt).not.toHaveBeenCalled()
  })

  it('keeps a typed tool_confirmation interrupt on the HITL callback', async () => {
    const confirmation = {
      type: 'tool_confirmation',
      message: 'Confirm?',
      tools: [{ id: 'tool-1', name: 'write_file' }]
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse({
      event: 'interrupt', data: confirmation
    })))
    const onPaused = vi.fn()
    const onInterrupt = vi.fn()

    await streamMessage({
      session_id: 'session-1',
      content: 'write',
      onPaused,
      onInterrupt
    })

    expect(onInterrupt).toHaveBeenCalledWith(confirmation)
    expect(onPaused).not.toHaveBeenCalled()
  })

  it('sends the exact trace id when cancelling a task', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'cancelled' }))
    vi.stubGlobal('fetch', fetchMock)

    await cancelStream('session with space', 'trace/exact?1')

    const [url, options] = fetchMock.mock.calls[0]
    const parsed = new URL(url, 'http://localhost')
    expect(parsed.pathname).toBe('/api/chat/stream/cancel')
    expect(parsed.searchParams.get('session_id')).toBe('session with space')
    expect(parsed.searchParams.get('trace_id')).toBe('trace/exact?1')
    expect(options.method).toBe('POST')
  })
})

describe('API client workspace editing', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('sends file content with the exact version digest', async () => {
    const response = {
      path: '项目/README.md',
      sha256: 'b'.repeat(64),
      size: 16,
      lines: 1,
      modified_at: 123
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response))
    vi.stubGlobal('fetch', fetchMock)

    await expect(saveFile('项目/README.md', '# updated\n', 'a'.repeat(64)))
      .resolves.toEqual(response)

    const [url, options] = fetchMock.mock.calls[0]
    expect(new URL(url, 'http://localhost').pathname).toBe('/api/workspace/write')
    expect(options.method).toBe('PUT')
    expect(JSON.parse(options.body)).toEqual({
      path: '项目/README.md',
      content: '# updated\n',
      expected_sha256: 'a'.repeat(64)
    })
  })

  it('preserves structured version-conflict details for the editor', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({
        detail: {
          code: 'version_conflict',
          message: 'The file changed after it was opened.',
          current_sha256: 'c'.repeat(64)
        }
      })
    }))

    await expect(saveFile('README.md', 'stale draft', 'a'.repeat(64)))
      .rejects.toMatchObject({
        message: 'The file changed after it was opened.',
        status: 409,
        code: 'version_conflict',
        currentSha256: 'c'.repeat(64)
      })
  })
})
