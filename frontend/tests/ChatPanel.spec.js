import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from '../src/components/ChatPanel.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  getSessionMessages: vi.fn(),
  getAgentCapabilities: vi.fn(),
  streamMessage: vi.fn(),
  resumeStream: vi.fn(),
  getStreamStatus: vi.fn().mockResolvedValue({ status: 'terminal' }),
  cancelStream: vi.fn(),
}))

const savedHistory = {
  session_id: 'session-existing',
  message_count: 2,
  history_status: 'durable',
  messages: [
    { role: 'user', content: '你好啊' },
    { role: 'assistant', content: '你好，有什么可以帮你？' }
  ]
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function mountChatPanel() {
  return mount(ChatPanel, {
    props: { sessionId: 'session-existing' },
    global: {
      stubs: {
        ToolCallCard: true
      }
    }
  })
}

describe('ChatPanel session history restoration', () => {
  beforeEach(() => {
    api.getSessionMessages.mockReset()
    api.getSessionMessages.mockResolvedValue(savedHistory)
    api.getAgentCapabilities.mockReset()
    api.getAgentCapabilities.mockResolvedValue({
      default_mode: 'single_agent',
      available_modes: ['single_agent', 'multi_agent'],
      multi_agent_enabled: true,
      multi_agent_permitted: true,
      multi_agent_reason: null
    })
    api.streamMessage.mockReset()
    api.resumeStream.mockReset()
    api.getStreamStatus.mockReset()
    api.getStreamStatus.mockResolvedValue({ status: 'terminal' })
    api.cancelStream.mockReset()
    api.cancelStream.mockImplementation(async (sessionId, traceId) => {
      const cancelledTraceId = traceId || 'trace-from-active-lease'
      api.getStreamStatus.mockResolvedValueOnce({
        status: 'cancelled',
        session_id: sessionId,
        trace_id: cancelledTraceId
      })
      return {
        status: 'cancelled',
        session_id: sessionId,
        trace_id: cancelledTraceId
      }
    })
  })

  it('loads legacy role/content history when mounted with an already-selected session', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(api.getSessionMessages).toHaveBeenCalledTimes(1)
    expect(api.getSessionMessages).toHaveBeenCalledWith('session-existing')
    expect(wrapper.findAll('[data-entry-role]').map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'assistant'
    ])
    expect(wrapper.text()).toContain('你好啊')
    expect(wrapper.text()).toContain('你好，有什么可以帮你？')

    wrapper.unmount()
  })

  it('replays a saved assistant timeline in text, tool, text order', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 2,
      history_status: 'durable',
      messages: [
        { role: 'user', content: '检查当前配置' },
        {
          role: 'assistant',
          content: '旧的聚合正文不应被显示',
          timeline: [
            { role: 'assistant', content: '先读取配置。' },
            { role: 'assistant', content: '   ' },
            {
              role: 'tool_call',
              toolCallId: 'call-history-read',
              toolName: 'read_file',
              toolStatus: 'done',
              toolResult: 'DEBUG=false',
              toolError: '',
              toolDuration: 18
            },
            { role: 'assistant', content: '配置读取完成。' }
          ]
        }
      ]
    })

    const wrapper = mountChatPanel()
    await flushPromises()

    const entries = wrapper.findAll('[data-entry-role]')
    expect(entries.map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'assistant', 'tool_call', 'assistant'
    ])
    expect(entries[1].text()).toContain('先读取配置。')
    expect(entries[2].find('tool-call-card-stub').attributes()).toMatchObject({
      name: 'read_file',
      status: 'done',
      result: 'DEBUG=false',
      duration: '18'
    })
    expect(entries[3].text()).toContain('配置读取完成。')
    expect(wrapper.text()).not.toContain('旧的聚合正文不应被显示')
    wrapper.unmount()
  })

  it('does not send when Enter is confirming an IME composition', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    const textarea = wrapper.find('textarea')

    await textarea.setValue('中文 mini')
    await textarea.trigger('compositionstart')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(api.streamMessage).not.toHaveBeenCalled()

    await textarea.trigger('compositionend')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(api.streamMessage).not.toHaveBeenCalled()

    await new Promise(resolve => setTimeout(resolve, 0))
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(api.streamMessage).toHaveBeenCalledTimes(1)
    expect(api.streamMessage.mock.calls[0][0]).toMatchObject({ content: '中文 mini' })
    wrapper.unmount()
  })

  it('restores the same session if the panel is destroyed and mounted again', async () => {
    const firstPanel = mountChatPanel()
    await flushPromises()
    firstPanel.unmount()

    const restoredPanel = mountChatPanel()
    await flushPromises()

    expect(api.getSessionMessages).toHaveBeenCalledTimes(2)
    expect(restoredPanel.text()).toContain('你好啊')
    expect(restoredPanel.text()).toContain('你好，有什么可以帮你？')

    restoredPanel.unmount()
  })

  it('explains when a legacy Redis-only history has expired', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'expired',
      messages: []
    })

    const wrapper = mountChatPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('History expired')
    expect(wrapper.text()).toContain('Redis-only messages have expired')
    wrapper.unmount()
  })

  it('shows a visible gap notice while keeping newer durable messages', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      ...savedHistory,
      history_status: 'partial'
    })

    const wrapper = mountChatPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('Earlier Redis-only messages expired')
    expect(wrapper.text()).toContain('你好啊')
    wrapper.unmount()
  })

  it('keeps an existing session locked when authoritative status lookup fails', async () => {
    api.getStreamStatus.mockRejectedValueOnce(new Error('status service unavailable'))
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('Task status unknown')
    expect(wrapper.text()).toContain('status service unavailable')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="check-stream-status"]').exists()).toBe(true)

    api.getStreamStatus.mockResolvedValueOnce({ status: 'terminal' })
    await wrapper.find('[data-testid="check-stream-status"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('sends the explicitly selected Multi-Agent mode', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()

    const multiButton = wrapper.findAll('.mode-option')[1]
    await multiButton.trigger('click')
    expect(multiButton.classes()).toContain('active')
    await wrapper.find('textarea').setValue('请让多个角色协作写一个故事')
    expect(wrapper.find('.btn-send').attributes('disabled')).toBeUndefined()
    await wrapper.find('.btn-send').trigger('click')

    expect(api.streamMessage).toHaveBeenCalledTimes(1)
    expect(api.streamMessage.mock.calls[0][0]).toMatchObject({
      session_id: 'session-existing',
      content: '请让多个角色协作写一个故事',
      mode: 'multi_agent'
    })

    wrapper.unmount()
  })

  it('requires an explicit switch instead of simulating a Multi-Agent request in Single mode', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()

    await wrapper.find('textarea').setValue('运用你的多智能体协作能力，写一篇短篇小说')
    await wrapper.find('.btn-send').trigger('click')

    expect(api.streamMessage).not.toHaveBeenCalled()
    const switchButton = document.body.querySelector('.mode-confirm-card .btn-approve')
    expect(switchButton).not.toBeNull()
    expect(document.body.textContent).toContain('will not create Python scripts')

    switchButton.click()
    await flushPromises()

    expect(api.streamMessage).toHaveBeenCalledTimes(1)
    expect(api.streamMessage.mock.calls[0][0]).toMatchObject({
      content: '运用你的多智能体协作能力，写一篇短篇小说',
      mode: 'multi_agent'
    })
    wrapper.unmount()
  })

  it('uses authoritative tool status instead of marking failures done', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('检查命令安全性')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onToolStart('bash', 'call-1')
    streamOptions.onToolResult('call-1', 'Blocked: denied', {
      id: 'call-1', name: 'bash', status: 'blocked', ok: false, error_code: 'policy_blocked'
    })
    streamOptions.onToolEnd('bash', {
      id: 'call-1', name: 'bash', status: 'blocked', ok: false,
      error_code: 'policy_blocked', duration_ms: 7
    })
    await flushPromises()

    const tool = wrapper.find('tool-call-card-stub')
    expect(tool.attributes('status')).toBe('error')
    expect(tool.attributes('result')).toContain('Blocked: denied')
    expect(tool.attributes('error')).toContain('policy_blocked')
    expect(tool.attributes('duration')).toBe('7')
    wrapper.unmount()
  })

  it('marks a tool error when the stream ends without a completion event', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('读取文件')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onToolStart('read_file', 'call-open')
    streamOptions.onDone()
    await flushPromises()

    const tool = wrapper.find('tool-call-card-stub')
    expect(tool.attributes('status')).toBe('error')
    expect(tool.attributes('error')).toContain('authoritative tool result')
    wrapper.unmount()
  })

  it('keeps assistant text and tool calls in SSE arrival order', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'durable',
      messages: []
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('按顺序检查配置')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-ordered',
      status: 'pending'
    })
    streamOptions.onDelta('先读取当前配置。')
    streamOptions.onToolStart('read_file', 'call-read')
    streamOptions.onToolResult('call-read', 'DEBUG=false', {
      id: 'call-read', name: 'read_file', status: 'success', ok: true
    })
    streamOptions.onToolEnd('read_file', {
      id: 'call-read', name: 'read_file', status: 'success', ok: true, duration_ms: 18
    })
    streamOptions.onDelta('配置读取完成。')
    streamOptions.onDone()
    await flushPromises()

    const entries = wrapper.findAll('[data-entry-role]')
    expect(entries.map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'assistant', 'tool_call', 'assistant'
    ])
    expect(entries[1].text()).toContain('先读取当前配置。')
    expect(entries[2].find('tool-call-card-stub').attributes('name')).toBe('read_file')
    expect(entries[2].find('tool-call-card-stub').attributes('status')).toBe('done')
    expect(entries[3].text()).toContain('配置读取完成。')
    expect(entries[1].text()).not.toContain('配置读取完成。')
    wrapper.unmount()
  })

  it('creates a tool entry when a provider omits tool_start', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'durable',
      messages: []
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('运行工具并汇报')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-missing-start',
      status: 'pending'
    })
    streamOptions.onToolResult('call-late', 'ok', {
      id: 'call-late', name: 'bash', status: 'success', ok: true
    })
    streamOptions.onToolEnd('bash', {
      id: 'call-late', name: 'bash', status: 'success', ok: true, duration_ms: 4
    })
    streamOptions.onDelta('工具已经完成。')
    await flushPromises()

    const entries = wrapper.findAll('[data-entry-role]')
    expect(entries.map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'tool_call', 'assistant'
    ])
    expect(entries[1].find('tool-call-card-stub').attributes('result')).toBe('ok')
    expect(entries[2].text()).toContain('工具已经完成。')
    wrapper.unmount()
  })

  it('deduplicates repeated tool_start events without splitting later text', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'durable',
      messages: []
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('检查重复事件')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-duplicate-start',
      status: 'pending'
    })
    streamOptions.onToolStart('bash', 'call-same')
    streamOptions.onToolStart('bash', 'call-same')
    streamOptions.onDelta('只创建一张工具卡。')
    await flushPromises()

    expect(wrapper.findAll('[data-entry-role="tool_call"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-entry-role="assistant"]')).toHaveLength(1)
    expect(wrapper.find('[data-entry-role="assistant"]').text()).toContain('只创建一张工具卡。')
    wrapper.unmount()
  })

  it('places resumed assistant text after a confirmed tool card', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'durable',
      messages: []
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('执行需要确认的操作')
    await wrapper.find('.btn-send').trigger('click')

    const initialStream = api.streamMessage.mock.calls[0][0]
    initialStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-confirm-order',
      status: 'pending'
    })
    initialStream.onDelta('这一步需要确认。')
    initialStream.onToolStart('bash', 'call-confirm')
    initialStream.onInterrupt({
      message: 'Confirm tool execution?',
      tools: [{ id: 'call-confirm', name: 'bash', description: 'Run command' }]
    })
    await flushPromises()

    document.body.querySelector('.btn-approve').click()
    await flushPromises()
    const resumedStream = api.resumeStream.mock.calls[0][0]
    resumedStream.onToolResult('call-confirm', 'command complete', {
      id: 'call-confirm', name: 'bash', status: 'success', ok: true
    })
    resumedStream.onToolEnd('bash', {
      id: 'call-confirm', name: 'bash', status: 'success', ok: true, duration_ms: 12
    })
    resumedStream.onDelta('操作已经完成。')
    await flushPromises()

    const entries = wrapper.findAll('[data-entry-role]')
    expect(entries.map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'assistant', 'tool_call', 'assistant'
    ])
    expect(entries[1].text()).toContain('这一步需要确认。')
    expect(entries[2].find('tool-call-card-stub').attributes('status')).toBe('done')
    expect(entries[3].text()).toContain('操作已经完成。')
    wrapper.unmount()
  })

  it('places a stop notice after a running tool when no text delta exists', async () => {
    api.getSessionMessages.mockResolvedValueOnce({
      session_id: 'session-existing',
      message_count: 0,
      history_status: 'durable',
      messages: []
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('运行后停止')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-stop-at-tool',
      status: 'pending'
    })
    streamOptions.onToolStart('bash', 'call-running')
    await flushPromises()
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    const entries = wrapper.findAll('[data-entry-role]')
    expect(entries.map(entry => entry.attributes('data-entry-role'))).toEqual([
      'user', 'tool_call', 'assistant'
    ])
    expect(entries[1].find('tool-call-card-stub').attributes('status')).toBe('error')
    expect(entries[2].text()).toContain('Generation stopped before any response was received')
    wrapper.unmount()
  })

  it('pauses auto-follow while the user reads earlier messages during streaming', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    const messageList = wrapper.find('.messages')
    let scrollTop = 0
    Object.defineProperties(messageList.element, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 200 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: value => { scrollTop = Math.min(value, 800) }
      }
    })

    await wrapper.find('textarea').setValue('继续生成长内容')
    await wrapper.find('.btn-send').trigger('click')
    await flushPromises()

    // A 10px trackpad gesture is still inside the old 80px threshold. It must
    // immediately express reading intent instead of being undone by SSE.
    messageList.element.scrollTop = 790
    await messageList.trigger('wheel', { deltaY: -10 })
    await messageList.trigger('scroll')
    expect(wrapper.find('.jump-to-latest').exists()).toBe(true)

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onDelta('新的流式内容')
    await flushPromises()
    expect(messageList.element.scrollTop).toBe(790)

    await wrapper.find('.jump-to-latest').trigger('click')
    await flushPromises()
    expect(messageList.element.scrollTop).toBe(800)
    expect(wrapper.find('.jump-to-latest').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps Multi-Agent disabled when the server does not expose it', async () => {
    api.getAgentCapabilities.mockResolvedValue({
      default_mode: 'single_agent',
      available_modes: ['single_agent'],
      multi_agent_enabled: false,
      multi_agent_permitted: true,
      multi_agent_reason: 'Multi-Agent mode is disabled by server configuration.'
    })
    const wrapper = mountChatPanel()
    await flushPromises()

    const multiButton = wrapper.findAll('.mode-option')[1]
    expect(multiButton.attributes('disabled')).toBeDefined()
    expect(multiButton.attributes('title')).toContain('disabled by server')

    wrapper.unmount()
  })

  it('sends an explicit rejection back to the interrupted graph', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('执行一个需要确认的任务')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onToolStart('delegate_task', 'delegate-1')
    streamOptions.onInterrupt({
      message: 'Confirm tool execution?',
      tools: [{ id: 'delegate-1', name: 'delegate_task', description: 'Delegate' }]
    })
    await flushPromises()

    expect(wrapper.find('tool-call-card-stub').attributes('status')).toBe('waiting')

    const rejectButton = document.body.querySelector('.btn-reject')
    expect(rejectButton).not.toBeNull()
    expect(document.body.textContent).toContain('Approve Current Batch')
    expect(document.body.textContent).toContain('Approval applies only to this current batch')
    expect(document.body.textContent).toContain('review')
    rejectButton.click()
    await flushPromises()

    expect(api.resumeStream).toHaveBeenCalledTimes(1)
    expect(api.resumeStream.mock.calls[0][0]).toMatchObject({
      session_id: 'session-existing',
      approved: false,
      approved_ids: []
    })
    expect(wrapper.find('tool-call-card-stub').attributes('status')).toBe('error')

    wrapper.unmount()
  })

  it('restores a legacy paused checkpoint as Stop-only and terminally cancels it', async () => {
    api.getStreamStatus.mockResolvedValueOnce({
      status: 'paused',
      trace_id: 'trace-restored',
      interrupt_type: 'user_pause',
      interrupt: { type: 'user_pause', resume_target: 'llm_call' }
    })
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(api.getStreamStatus).toHaveBeenCalledWith('session-existing')
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('[data-testid="continue-task"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="pause-task"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="stop-task"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('can no longer be continued')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()
    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', 'trace-restored')
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps the SSE request alive until exact-trace cancellation is authoritative', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('你现在有什么tools')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-stop-running',
      status: 'pending'
    })
    await flushPromises()

    api.cancelStream.mockImplementationOnce(async (sessionId, traceId) => {
      expect(sessionId).toBe('session-existing')
      expect(traceId).toBe('trace-stop-running')
      expect(streamOptions.signal.aborted).toBe(false)
      api.getStreamStatus.mockResolvedValueOnce({
        status: 'cancelled',
        trace_id: 'trace-stop-running'
      })
      return { status: 'cancelled', trace_id: 'trace-stop-running' }
    })
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(true)
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledTimes(1)
    expect(streamOptions.signal.aborted).toBe(true)
    expect(wrapper.text()).toContain('Generation stopped before any response was received')
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps input locked after cancel failure and allows an exact-trace retry', async () => {
    const firstCancel = deferred()
    api.cancelStream.mockImplementationOnce(() => firstCancel.promise)
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('请执行长任务')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-retry-cancel'
    })
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Cancelling')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(streamOptions.signal.aborted).toBe(false)

    firstCancel.reject(new Error('Redis unavailable'))
    await flushPromises()

    expect(wrapper.text()).toContain('Cancellation needs attention')
    expect(wrapper.text()).toContain('Redis unavailable')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="check-stream-status"]').exists()).toBe(true)
    expect(api.streamMessage).toHaveBeenCalledTimes(1)

    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledTimes(2)
    expect(api.cancelStream).toHaveBeenLastCalledWith('session-existing', 'trace-retry-cancel')
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    expect(streamOptions.signal.aborted).toBe(true)
    wrapper.unmount()
  })

  it('cancels through the active lease when Stop happens before task_started', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('立即停止')
    await wrapper.find('.btn-send').trigger('click')
    const streamOptions = api.streamMessage.mock.calls[0][0]

    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', '')
    expect(api.getStreamStatus).toHaveBeenLastCalledWith('session-existing')
    expect(streamOptions.signal.aborted).toBe(true)
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps a wrong-trace cancel response locked until status confirms the original trace', async () => {
    api.cancelStream.mockResolvedValueOnce({
      status: 'cancelled',
      trace_id: 'trace-wrong'
    })
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('取消精确 trace')
    await wrapper.find('.btn-send').trigger('click')
    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-right'
    })

    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('different trace')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(streamOptions.signal.aborted).toBe(false)

    api.getStreamStatus.mockResolvedValueOnce({ status: 'cancelled', trace_id: 'trace-right' })
    await wrapper.find('[data-testid="check-stream-status"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    expect(streamOptions.signal.aborted).toBe(true)
    wrapper.unmount()
  })

  it('verifies a cancelled SSE event against status before unlocking', async () => {
    const statusCheck = deferred()
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('等待权威取消')
    await wrapper.find('.btn-send').trigger('click')
    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-sse-cancelled'
    })
    api.getStreamStatus.mockImplementationOnce(() => statusCheck.promise)

    streamOptions.onCancelled({
      event: 'cancelled',
      session_id: 'session-existing',
      trace_id: 'trace-sse-cancelled',
      message: 'Cancellation observed'
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Cancelling')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Generation stopped by user')

    statusCheck.resolve({ status: 'cancelled', trace_id: 'trace-sse-cancelled' })
    await flushPromises()

    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('Generation stopped before any response was received')
    wrapper.unmount()
  })

  it('terminates a waiting confirmation without resuming or rejecting its graph', async () => {
    const cancelRequest = deferred()
    api.cancelStream.mockImplementationOnce(() => cancelRequest.promise)
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('执行敏感工具')
    await wrapper.find('.btn-send').trigger('click')
    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-confirm-stop'
    })
    streamOptions.onToolStart('write_file', 'call-confirm-stop')
    streamOptions.onInterrupt({
      type: 'tool_confirmation',
      message: 'Confirm write?',
      tools: [{ id: 'call-confirm-stop', name: 'write_file', description: 'Write file' }]
    })
    await flushPromises()

    const terminateButton = document.body.querySelector('[data-testid="terminate-task"]')
    expect(terminateButton).not.toBeNull()
    terminateButton.click()
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', 'trace-confirm-stop')
    expect(api.resumeStream).not.toHaveBeenCalled()
    expect(document.body.querySelector('.btn-reject').disabled).toBe(true)
    expect(document.body.querySelector('.btn-approve').disabled).toBe(true)
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()

    api.getStreamStatus.mockResolvedValueOnce({ status: 'cancelled', trace_id: 'trace-confirm-stop' })
    cancelRequest.resolve({ status: 'cancelled', trace_id: 'trace-confirm-stop' })
    await flushPromises()

    expect(document.body.querySelector('[data-testid="terminate-task"]')).toBeNull()
    expect(api.resumeStream).not.toHaveBeenCalled()
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('ignores every late event from a cancelled trace after a new trace starts', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('第一个任务')
    await wrapper.find('.btn-send').trigger('click')
    const oldStream = api.streamMessage.mock.calls[0][0]
    oldStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-old'
    })
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    await wrapper.find('textarea').setValue('第二个任务')
    await wrapper.find('.btn-send').trigger('click')
    const newStream = api.streamMessage.mock.calls[1][0]
    newStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-new'
    })
    newStream.onDelta('NEW_TRACE_CONTENT')

    oldStream.onDelta('LATE_OLD_DELTA')
    oldStream.onToolStart('late_old_tool', 'late-call')
    oldStream.onToolResult('late-call', 'late result', { name: 'late_old_tool' })
    oldStream.onDone()
    oldStream.onCancelled({ trace_id: 'trace-old' })
    await flushPromises()

    expect(wrapper.text()).toContain('NEW_TRACE_CONTENT')
    expect(wrapper.text()).not.toContain('LATE_OLD_DELTA')
    expect(wrapper.text()).not.toContain('late_old_tool')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(api.getStreamStatus).toHaveBeenCalledTimes(2)
    newStream.onDone()
    wrapper.unmount()
  })

  it('rejects mismatched trace metadata inside the current stream epoch', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('校验 SSE trace')
    await wrapper.find('.btn-send').trigger('click')
    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-authoritative'
    })

    streamOptions.onDelta('WRONG_TRACE_DELTA', { trace_id: 'trace-stale' })
    streamOptions.onToolStart('wrong_trace_tool', 'wrong-call', { trace_id: 'trace-stale' })
    streamOptions.onCancelled({ event: 'cancelled', trace_id: 'trace-stale' })
    streamOptions.onDelta('RIGHT_TRACE_DELTA', { trace_id: 'trace-authoritative' })
    await flushPromises()

    expect(wrapper.text()).not.toContain('WRONG_TRACE_DELTA')
    expect(wrapper.text()).not.toContain('wrong_trace_tool')
    expect(wrapper.text()).toContain('RIGHT_TRACE_DELTA')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(api.getStreamStatus).toHaveBeenCalledTimes(1)
    streamOptions.onDone()
    wrapper.unmount()
  })

  it('ignores a late cancel response after switching sessions', async () => {
    const oldCancel = deferred()
    api.cancelStream.mockImplementationOnce(() => oldCancel.promise)
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('旧 session 任务')
    await wrapper.find('.btn-send').trigger('click')
    const oldStream = api.streamMessage.mock.calls[0][0]
    oldStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-old-session'
    })
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    await wrapper.setProps({ sessionId: 'session-other' })
    await flushPromises()
    oldCancel.resolve({ status: 'cancelled', trace_id: 'trace-old-session' })
    await flushPromises()

    await wrapper.find('textarea').setValue('新 session 任务')
    await wrapper.find('.btn-send').trigger('click')
    const currentStream = api.streamMessage.mock.calls[1][0]
    currentStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-other',
      trace_id: 'trace-current-session'
    })
    currentStream.onDelta('CURRENT_AFTER_OLD_CANCEL')
    await flushPromises()

    expect(wrapper.text()).toContain('CURRENT_AFTER_OLD_CANCEL')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(api.getStreamStatus).toHaveBeenCalledTimes(2)
    currentStream.onDone()
    wrapper.unmount()
  })

  it('restores a tool-confirmation interrupt when the status payload is available', async () => {
    api.getStreamStatus.mockResolvedValueOnce({
      status: 'waiting',
      trace_id: 'trace-confirmation',
      interrupt_type: 'tool_confirmation',
      interrupt: {
        type: 'tool_confirmation',
        message: 'Confirm restored tool?',
        tools: [{ id: 'restored-1', name: 'write_file', description: 'Write a file' }]
      }
    })
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(document.body.textContent).toContain('Confirm restored tool?')
    const rejectButton = document.body.querySelector('.modal-card .btn-reject')
    expect(rejectButton).not.toBeNull()
    rejectButton.click()
    await flushPromises()

    expect(api.resumeStream.mock.calls[0][0]).toMatchObject({
      session_id: 'session-existing',
      approved: false,
      approved_ids: []
    })

    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()
    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', 'trace-confirmation')
    wrapper.unmount()
  })

  it('detaches from a stream on session switch without implicitly cancelling it', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('后台继续执行')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-detach',
      status: 'pending'
    })
    await wrapper.setProps({ sessionId: 'session-other' })
    await flushPromises()

    expect(streamOptions.signal.aborted).toBe(true)
    expect(api.cancelStream).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ignores a late status response from the previously selected session', async () => {
    const oldStatus = deferred()
    api.getStreamStatus.mockImplementationOnce(() => oldStatus.promise)
    const wrapper = mountChatPanel()
    await flushPromises()

    await wrapper.setProps({ sessionId: 'session-other' })
    await flushPromises()
    await wrapper.find('textarea').setValue('新 session 任务')
    await wrapper.find('.btn-send').trigger('click')
    const newStream = api.streamMessage.mock.calls[0][0]
    newStream.onTaskStarted({
      event: 'task_started',
      session_id: 'session-other',
      trace_id: 'trace-session-other'
    })

    oldStatus.resolve({ status: 'running', trace_id: 'trace-stale-session' })
    await flushPromises()
    newStream.onDelta('CURRENT_SESSION_CONTENT')
    await flushPromises()

    expect(wrapper.text()).toContain('CURRENT_SESSION_CONTENT')
    expect(wrapper.text()).not.toContain('can no longer be continued')
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()
    expect(api.cancelStream).toHaveBeenCalledWith('session-other', 'trace-session-other')
    wrapper.unmount()
  })
})
