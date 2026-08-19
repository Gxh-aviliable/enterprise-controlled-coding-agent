import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamMessage } from '../src/api/client.js'

function sseResponse(events) {
  const raw = events
    .map(event => `data: ${typeof event === 'string' ? event : JSON.stringify(event)}\n\n`)
    .join('')
  const bytes = new TextEncoder().encode(raw)
  const firstBreak = Math.floor(bytes.length / 3)
  const secondBreak = Math.floor(bytes.length * 2 / 3)
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, firstBreak))
      controller.enqueue(bytes.slice(firstBreak, secondBreak))
      controller.enqueue(bytes.slice(secondBreak))
      controller.close()
    }
  })

  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

describe('API client canonical chat stream', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('dispatches raw delta and tool SSE events in arrival order', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { delta: '先读取文件。' },
      { event: 'tool_start', id: 'call-read', name: 'read_file' },
      {
        event: 'tool_result',
        id: 'call-read',
        name: 'read_file',
        result: '# Project',
        status: 'success',
        ok: true,
        duration_ms: 12
      },
      {
        event: 'tool_end',
        id: 'call-read',
        name: 'read_file',
        status: 'success',
        ok: true,
        duration_ms: 12
      },
      { delta: '文件读取完成。' },
      '[DONE]'
    ])))
    const dispatches = []

    await streamMessage({
      session_id: 'session-ordered',
      content: '读取 README',
      onDelta: delta => dispatches.push(['delta', delta]),
      onToolStart: (name, id) => dispatches.push(['tool_start', name, id]),
      onToolResult: (id, result, metadata) => dispatches.push([
        'tool_result', id, result, metadata.name, metadata.status, metadata.ok
      ]),
      onToolEnd: (name, metadata) => dispatches.push([
        'tool_end', name, metadata.id, metadata.status, metadata.ok
      ]),
      onDone: () => dispatches.push(['done'])
    })

    expect(dispatches).toEqual([
      ['delta', '先读取文件。'],
      ['tool_start', 'read_file', 'call-read'],
      ['tool_result', 'call-read', '# Project', 'read_file', 'success', true],
      ['tool_end', 'read_file', 'call-read', 'success', true],
      ['delta', '文件读取完成。'],
      ['done']
    ])
  })

  it('treats transport EOF without a terminal event as an error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { event: 'task_started', session_id: 'session-eof', trace_id: 'trace-eof' },
      { delta: 'partial response' }
    ])))
    const onDone = vi.fn()
    const onError = vi.fn()

    await streamMessage({
      session_id: 'session-eof',
      content: 'run',
      onDone,
      onError
    })

    expect(onDone).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('transport closed'))
  })
})
