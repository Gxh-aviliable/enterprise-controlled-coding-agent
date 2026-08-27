import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolCallCard from '../src/components/ToolCallCard.vue'

describe('ToolCallCard', () => {
  it('renders a compact accessible summary and reveals output on demand', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        name: 'search_memory',
        status: 'done',
        duration: 42,
        result: '2 matching memories'
      }
    })

    const toggle = wrapper.get('button.tool-header')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(toggle.attributes('aria-label')).toContain('search_memory: 42 ms')
    expect(wrapper.text()).toContain('Tool')
    expect(wrapper.text()).toContain('search_memory')
    expect(wrapper.text()).toContain('42 ms')
    expect(wrapper.get('.tool-body').attributes('style')).toContain('display: none')

    await toggle.trigger('click')

    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.tool-body').attributes('style') || '').not.toContain('display: none')
    expect(wrapper.text()).toContain('Execution output')
    expect(wrapper.text()).toContain('2 matching memories')
    wrapper.unmount()
  })

  it('uses direct action language for approval and failure states', async () => {
    const wrapper = mount(ToolCallCard, {
      props: { name: 'bash', status: 'waiting' }
    })

    expect(wrapper.get('[data-tool-status="waiting"]').text()).toContain('Approval needed')
    await wrapper.setProps({ status: 'error', error: 'Tool failed: policy_blocked' })
    expect(wrapper.get('[data-tool-status="error"]').text()).toContain('Failed')

    await wrapper.get('button.tool-header').trigger('click')
    expect(wrapper.text()).toContain('Tool failed: policy_blocked')
    wrapper.unmount()
  })

  it('distinguishes a declined approval from an execution failure', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        name: 'write_file',
        status: 'rejected',
        error: 'Not approved — this tool was not run.'
      }
    })

    const card = wrapper.get('[data-tool-status="rejected"]')
    expect(card.text()).toContain('Not approved')
    expect(card.text()).not.toContain('Failed')

    await wrapper.get('button.tool-header').trigger('click')
    expect(wrapper.text()).toContain('Execution decision')
    expect(wrapper.text()).toContain('this tool was not run')
    wrapper.unmount()
  })
})
