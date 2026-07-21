import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MemoryViewer from '../src/components/MemoryViewer.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  fetchMemories: vi.fn(),
  fetchPatterns: vi.fn(),
  deleteMemory: vi.fn(),
  deletePattern: vi.fn()
}))

describe('MemoryViewer quality governance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchMemories.mockResolvedValue({
      active_count: 1,
      legacy_count: 1,
      memories: [
        {
          id: 'active-1',
          content: 'Validated API fix',
          importance: 0.9,
          memory_type: 'task_outcome',
          task_status: 'succeeded',
          quality_status: 'active',
          admission_reason: 'verified_engineering_outcome',
          schema_version: 2,
          session_id: 'active-session',
          content_format: 'structured_task_summary',
          retrieval_enabled: true,
          retrieval_count: 3,
          last_retrieved_at: '2026-07-20T10:00:00Z'
        },
        {
          id: 'legacy-1',
          content: 'Old failed story attempt',
          importance: 1,
          memory_type: 'legacy_summary',
          task_status: 'unknown',
          quality_status: 'legacy',
          admission_reason: 'legacy_unclassified',
          schema_version: 1,
          session_id: 'legacy-session'
        }
      ]
    })
    api.fetchPatterns.mockResolvedValue({
      active_count: 1,
      legacy_count: 1,
      patterns: [
        {
          id: 'active-pattern',
          pattern_type: 'workflow',
          pattern_key: 'test_command',
          confidence: 0.95,
          evidence_count: 2,
          quality_status: 'active',
          retrieval_count: 0,
          source_memory_ids: ['active-1'],
          value: '{"command":"pytest"}'
        },
        {
          id: 'legacy-pattern',
          pattern_type: 'preference',
          pattern_key: 'fiction_genre',
          confidence: 1,
          evidence_count: 0,
          quality_status: 'legacy',
          quarantine_reason: 'missing_source_provenance',
          text: 'preference: fiction_genre = fantasy'
        }
      ]
    })
    api.deleteMemory.mockResolvedValue({
      status: 'deleted',
      id: 'active-1',
      deleted_pattern_ids: [],
      deleted_pattern_count: 0
    })
    api.deletePattern.mockResolvedValue({ status: 'deleted' })
  })

  it('distinguishes stored, recalled, and never-recalled evidence', async () => {
    const wrapper = mount(MemoryViewer)
    await flushPromises()

    expect(wrapper.text()).toContain('Recall-ready records')
    expect(wrapper.text()).toContain('Recalled')
    expect(wrapper.text()).toContain('Never recalled')
    expect(wrapper.text()).toContain('Legacy quarantine')
    expect(wrapper.text()).toContain('3 recalls')
    expect(wrapper.text()).toContain('not proof that the model used it correctly')
    expect(wrapper.text()).toContain('1 outcomes + 1 preferences')
    expect(wrapper.findAll('.tab-count')[0].text()).toBe('1')
    expect(wrapper.findAll('.tab-count')[1].text()).toBe('1')
    expect(wrapper.text()).toContain('Validated API fix')
    expect(wrapper.text()).not.toContain('Old failed story attempt')

    const legacyButton = wrapper.findAll('.quality-switch button')[1]
    await legacyButton.trigger('click')

    expect(wrapper.text()).toContain('Old failed story attempt')
    expect(wrapper.text()).toContain('created before the v2 admission policy')
    expect(wrapper.text()).not.toContain('Validated API fix')
  })

  it('shows legacy inferred preferences as zero-evidence quarantine records', async () => {
    const wrapper = mount(MemoryViewer)
    await flushPromises()

    await wrapper.findAll('.header-tabs button')[1].trigger('click')
    await wrapper.findAll('.quality-switch button')[1].trigger('click')

    expect(wrapper.text()).toContain('fiction genre')
    expect(wrapper.text()).toContain('0 evidence')
    expect(wrapper.text()).toContain('its source memory cannot be verified')
    expect(wrapper.find('.pattern-card.quality-legacy').exists()).toBe(true)
  })

  it('marks an active preference as never recalled', async () => {
    const wrapper = mount(MemoryViewer)
    await flushPromises()

    await wrapper.findAll('.header-tabs button')[1].trigger('click')

    expect(wrapper.text()).toContain('test command')
    expect(wrapper.text()).toContain('Never recalled')
    expect(wrapper.find('.pattern-recall.never').exists()).toBe(true)
  })

  it('explains a cross-tab total instead of showing a misleading empty ledger', async () => {
    api.fetchMemories.mockResolvedValueOnce({
      active_count: 0,
      legacy_count: 0,
      memories: []
    })
    api.fetchPatterns.mockResolvedValueOnce({
      active_count: 3,
      legacy_count: 0,
      patterns: [
        { id: 'p1', pattern_type: 'preference', pattern_key: 'one', quality_status: 'active' },
        { id: 'p2', pattern_type: 'preference', pattern_key: 'two', quality_status: 'active' },
        { id: 'p3', pattern_type: 'workflow', pattern_key: 'three', quality_status: 'active' }
      ]
    })
    const wrapper = mount(MemoryViewer)
    await flushPromises()

    expect(wrapper.text()).toContain('0 outcomes + 3 preferences')
    const openPreferences = wrapper.find('.empty-action')
    expect(openPreferences.text()).toContain('Open 3 active preferences')
    await openPreferences.trigger('click')

    expect(wrapper.findAll('.pattern-card')).toHaveLength(3)
  })

  it('reloads both ledgers and reports linked preference deletion', async () => {
    api.fetchMemories
      .mockResolvedValueOnce({
        memories: [{
          id: 'active-1',
          content: 'Validated API fix',
          importance: 0.9,
          memory_type: 'task_outcome',
          task_status: 'succeeded',
          quality_status: 'active',
          admission_reason: 'verified_engineering_outcome',
          schema_version: 2
        }]
      })
      .mockResolvedValueOnce({ memories: [] })
    api.fetchPatterns
      .mockResolvedValueOnce({
        patterns: [{
          id: 'active-pattern',
          pattern_type: 'workflow',
          pattern_key: 'test_command',
          quality_status: 'active',
          source_memory_ids: ['active-1']
        }]
      })
      .mockResolvedValueOnce({ patterns: [] })
    api.deleteMemory.mockResolvedValueOnce({
      status: 'deleted',
      id: 'active-1',
      deleted_pattern_ids: ['active-pattern'],
      deleted_pattern_count: 1
    })

    const wrapper = mount(MemoryViewer)
    await flushPromises()
    await wrapper.find('.btn-delete').trigger('click')
    await wrapper.find('.btn-confirm-delete').trigger('click')
    await flushPromises()

    expect(api.deleteMemory).toHaveBeenCalledWith('active-1')
    expect(wrapper.text()).toContain('Deleted the memory and 1 linked preference.')
    expect(wrapper.findAll('.memory-card')).toHaveLength(0)
  })
})
