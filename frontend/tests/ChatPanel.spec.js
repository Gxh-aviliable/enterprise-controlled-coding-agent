import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from '../src/components/ChatPanel.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  getSessionMessages: vi.fn(),
  getAgentCapabilities: vi.fn(),
  streamMessage: vi.fn(),
  resumeStream: vi.fn(),
  pauseStream: vi.fn().mockResolvedValue({ status: 'pause_requested' }),
  continuePausedStream: vi.fn(),
  getStreamStatus: vi.fn().mockResolvedValue({ status: 'terminal' }),
  cancelStream: vi.fn().mockResolvedValue({}),
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
    api.pauseStream.mockReset()
    api.pauseStream.mockResolvedValue({ status: 'pause_requested' })
    api.continuePausedStream.mockReset()
    api.getStreamStatus.mockReset()
    api.getStreamStatus.mockResolvedValue({ status: 'terminal' })
    api.cancelStream.mockReset()
    api.cancelStream.mockResolvedValue({})
  })

  it('loads saved history when mounted with an already-selected session', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(api.getSessionMessages).toHaveBeenCalledTimes(1)
    expect(api.getSessionMessages).toHaveBeenCalledWith('session-existing')
    expect(wrapper.text()).toContain('你好啊')
    expect(wrapper.text()).toContain('你好，有什么可以帮你？')

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

  it('requests a safe-boundary pause without aborting and continues the same trace', async () => {
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('执行一个长任务')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-pause-1',
      status: 'pending'
    })
    streamOptions.onDelta('已经完成第一步')
    await flushPromises()

    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(true)
    expect(wrapper.find('.input-wrapper [data-testid="pause-task"]').exists()).toBe(false)
    expect(wrapper.find('.execution-mode-bar [data-testid="pause-task"]').exists()).toBe(true)
    expect(streamOptions.signal.aborted).toBe(false)
    await wrapper.find('[data-testid="pause-task"]').trigger('click')
    await flushPromises()

    expect(api.pauseStream).toHaveBeenCalledWith({
      session_id: 'session-existing',
      trace_id: 'trace-pause-1'
    })
    expect(wrapper.text()).toContain('Pausing at next safe boundary')
    expect(wrapper.find('.status-badge.pausing').exists()).toBe(true)
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('[data-testid="stop-task"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pause-task"]').attributes('disabled')).toBeDefined()
    expect(streamOptions.signal.aborted).toBe(false)

    streamOptions.onPaused({
      event: 'paused',
      session_id: 'session-existing',
      trace_id: 'trace-pause-1',
      status: 'paused'
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Paused')
    expect(wrapper.find('.status-badge.paused').exists()).toBe(true)
    expect(wrapper.text()).toContain('已经完成第一步')
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('[data-testid="continue-task"]').exists()).toBe(true)
    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cancel-paused-task"]').exists()).toBe(true)
    expect(api.cancelStream).not.toHaveBeenCalled()

    await wrapper.find('[data-testid="continue-task"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.status-badge.resuming').text()).toContain('Resuming')
    expect(api.continuePausedStream).toHaveBeenCalledTimes(1)
    const continueOptions = api.continuePausedStream.mock.calls[0][0]
    expect(continueOptions).toMatchObject({
      session_id: 'session-existing',
      trace_id: 'trace-pause-1'
    })
    continueOptions.onDelta('，继续完成第二步')
    continueOptions.onDone()
    await flushPromises()

    expect(wrapper.text()).toContain('已经完成第一步，继续完成第二步')
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('restores a durable paused checkpoint and continues after remount', async () => {
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
    expect(wrapper.find('[data-testid="continue-task"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="cancel-paused-task"]').exists()).toBe(true)
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid="continue-task"]').trigger('click')
    expect(api.continuePausedStream.mock.calls[0][0]).toMatchObject({
      session_id: 'session-existing',
      trace_id: 'trace-restored'
    })
    wrapper.unmount()
  })

  it('aborts a running SSE before cancelling its exact trace', async () => {
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
      expect(streamOptions.signal.aborted).toBe(true)
      return { status: 'cancelled' }
    })
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(true)
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Generation stopped before any response was received')
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('ignores a late pause response after Stop has already cancelled the task', async () => {
    let resolvePause
    api.pauseStream.mockImplementationOnce(() => new Promise(resolve => {
      resolvePause = resolve
    }))
    const wrapper = mountChatPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('执行一个长任务')
    await wrapper.find('.btn-send').trigger('click')

    const streamOptions = api.streamMessage.mock.calls[0][0]
    streamOptions.onTaskStarted({
      event: 'task_started',
      session_id: 'session-existing',
      trace_id: 'trace-pause-race',
      status: 'pending'
    })
    await flushPromises()

    const pauseClick = wrapper.find('[data-testid="pause-task"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.status-badge.pausing').exists()).toBe(true)

    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(true)
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    await wrapper.find('[data-testid="stop-task"]').trigger('click')
    await flushPromises()
    resolvePause({
      status: 'paused',
      trace_id: 'trace-pause-race'
    })
    await pauseClick
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', 'trace-pause-race')
    expect(wrapper.find('.status-badge.paused').exists()).toBe(false)
    expect(wrapper.find('[data-testid="continue-task"]').exists()).toBe(false)
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps Stop as a terminal cancellation while a task is paused', async () => {
    api.getStreamStatus.mockResolvedValueOnce({
      status: 'paused',
      trace_id: 'trace-cancel-paused',
      interrupt_type: 'user_pause',
      interrupt: { type: 'user_pause' }
    })
    const wrapper = mountChatPanel()
    await flushPromises()

    expect(wrapper.find('.input-wrapper [data-testid="stop-task"]').exists()).toBe(false)
    expect(wrapper.findAll('.input-wrapper > .btn-send')).toHaveLength(1)
    await wrapper.find('[data-testid="cancel-paused-task"]').trigger('click')
    await flushPromises()

    expect(api.cancelStream).toHaveBeenCalledWith('session-existing', 'trace-cancel-paused')
    expect(wrapper.find('[data-testid="continue-task"]').exists()).toBe(false)
    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
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
})
