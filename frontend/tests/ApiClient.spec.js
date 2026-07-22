import { afterEach, describe, expect, it, vi } from 'vitest'

import { sendMessage, streamMessage } from '../src/api/client.js'

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
