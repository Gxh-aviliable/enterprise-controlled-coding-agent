import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TraceViewer from '../src/components/TraceViewer.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  listTaskRuns: vi.fn(),
  getTaskMetrics: vi.fn(),
  replayTaskTrace: vi.fn()
}))

describe('TraceViewer memory evidence', () => {
  beforeEach(() => {
    api.listTaskRuns.mockResolvedValue({
      tasks: [{
        trace_id: 'trace-memory-1',
        request_summary: 'Run tests using my preferred workflow',
        status: 'succeeded',
        mode: 'single_agent',
        started_at: '2026-07-20T10:00:00Z',
        duration_ms: 1250
      }]
    })
    api.getTaskMetrics.mockResolvedValue({
      task_count: 2,
      succeeded: 2,
      task_success_rate: 1,
      tool_success_rate: 1,
      tool_calls: 3,
      average_duration_ms: 1250,
      average_tokens: 800,
      human_intervention_rate: 0,
      confirmation_count: 0,
      safety_interceptions: 0,
      memory_injection_rate: 0.5,
      memory_injected: 1,
      average_memory_tokens: 42
    })
    api.replayTaskTrace.mockResolvedValue({
      trace_id: 'trace-memory-1',
      request_summary: 'Run tests using my preferred workflow',
      status: 'succeeded',
      duration_ms: 1250,
      metrics: {
        model_calls: 1,
        tool_calls: 1,
        total_tokens: 800,
        memory_injected: 1
      },
      events: [{
        event_id: 'event-memory-1',
        type: 'memory',
        name: 'memory_retrieval',
        status: 'success',
        timestamp: '2026-07-20T10:00:00Z',
        duration_ms: 8,
        data: {
          query_summary: 'Run tests using my preferred workflow',
          strategy: 'semantic_top_k',
          threshold: 0.8,
          injected_count: 1,
          injected_tokens: 42,
          application_status: 'not_attributed',
          candidates: [
            {
              memory_id: 'memory-eligible-1234',
              collection: 'conversations',
              memory_type: 'user_note',
              rank: 1,
              distance: 0.21,
              eligible: true,
              filter_reason: 'eligible'
            },
            {
              memory_id: 'memory-filtered-5678',
              collection: 'patterns',
              memory_type: 'workflow',
              rank: 2,
              distance: 0.91,
              eligible: false,
              filter_reason: 'distance_above_threshold'
            }
          ]
        }
      }]
    })
  })

  it('renders recall receipts and separates injection from application', async () => {
    const wrapper = mount(TraceViewer)
    await flushPromises()

    expect(wrapper.text()).toContain('Memory-injected runs')
    expect(wrapper.text()).toContain('50%')
    expect(wrapper.text()).toContain('Recall receipt')
    expect(wrapper.text()).toContain('1 / 2 injected')
    expect(wrapper.text()).toContain('0.210')
    expect(wrapper.text()).toContain('distance_above_threshold')
    expect(wrapper.text()).toContain('Injected is not proof of application')
    expect(wrapper.find('.candidate-row.eligible').exists()).toBe(true)
    expect(wrapper.find('.candidate-row.filtered').exists()).toBe(true)
  })

  it('renders distinct pause lifecycle states in the run index and detail badge', async () => {
    api.listTaskRuns.mockResolvedValueOnce({
      tasks: [
        {
          trace_id: 'trace-paused', request_summary: 'Paused task', status: 'paused',
          mode: 'single_agent', started_at: '2026-08-10T10:00:00Z', duration_ms: 100
        },
        {
          trace_id: 'trace-pausing', request_summary: 'Pausing task', status: 'pause_requested',
          mode: 'single_agent', started_at: '2026-08-10T10:01:00Z', duration_ms: 50
        },
        {
          trace_id: 'trace-resuming', request_summary: 'Resuming task', status: 'resuming',
          mode: 'single_agent', started_at: '2026-08-10T10:02:00Z', duration_ms: 25
        }
      ]
    })
    api.replayTaskTrace.mockResolvedValueOnce({
      trace_id: 'trace-paused',
      request_summary: 'Paused task',
      status: 'paused',
      duration_ms: 100,
      metrics: { model_calls: 1, tool_calls: 0, total_tokens: 100, memory_injected: 0 },
      events: []
    })

    const wrapper = mount(TraceViewer)
    await flushPromises()

    expect(wrapper.find('.status-pip.paused').exists()).toBe(true)
    expect(wrapper.find('.status-pip.pause_requested').exists()).toBe(true)
    expect(wrapper.find('.status-pip.resuming').exists()).toBe(true)
    expect(wrapper.find('.status-badge.paused').text()).toBe('paused')
  })
})
