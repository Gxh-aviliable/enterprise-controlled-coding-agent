import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AdminConsole from '../src/components/admin/AdminConsole.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  getAdminOverview: vi.fn(),
  listAdminUsers: vi.fn(),
  getAdminUser: vi.fn(),
  updateAdminUserStatus: vi.fn(),
  updateAdminUserQuota: vi.fn(),
  getAdminWorkspaceTree: vi.fn(),
  createAdminAccessGrant: vi.fn(),
  readAdminWorkspaceFile: vi.fn(),
  listAdminSkills: vi.fn(),
  getAdminSkill: vi.fn(),
  saveAdminSkillDraft: vi.fn(),
  validateAdminSkill: vi.fn(),
  publishAdminSkill: vi.fn(),
  retireAdminSkill: vi.fn(),
  listAdminAuditLogs: vi.fn(),
  getAdminSystemHealth: vi.fn()
}))

const overview = {
  users: { total: 3, active: 2, admins: 1 },
  tasks: { task_count: 12, failed: 2, tool_calls: 30, safety_interceptions: 4, confirmation_count: 5 },
  recent_tasks: []
}

describe('AdminConsole', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getAdminOverview.mockResolvedValue(overview)
    api.listAdminUsers.mockResolvedValue({
      items: [{ id: 2, username: 'alice', email: 'alice@example.com', is_active: true }],
      total: 1
    })
    api.getAdminUser.mockResolvedValue({
      user: { id: 2, username: 'alice', email: 'alice@example.com', role: 'free', is_active: true },
      quota: {
        daily_task_limit: 50,
        daily_token_limit: 500000,
        monthly_token_limit: 5000000,
        concurrent_task_limit: 2,
        workspace_bytes_limit: 1073741824,
        enabled: true,
        version: 1
      },
      usage: { task_count: 4, task_success_rate: 0.75, average_tokens: 1200 },
      workspace: { bytes: 4096 }
    })
    api.getAdminWorkspaceTree.mockResolvedValue({
      path: '',
      name: 'user_2',
      type: 'dir',
      children: [{ path: 'app.py', name: 'app.py', type: 'file', size: 20, sensitive: false }]
    })
    api.createAdminAccessGrant.mockResolvedValue({
      id: '12345678-1234-1234-1234-123456789012',
      target_user_id: 2,
      expires_at: new Date(Date.now() + 600000).toISOString()
    })
    api.readAdminWorkspaceFile.mockResolvedValue({ path: 'app.py', content: 'print("safe")', binary: false })
    api.listAdminSkills.mockResolvedValue({ items: [] })
    api.listAdminAuditLogs.mockResolvedValue({ items: [] })
    api.getAdminSystemHealth.mockResolvedValue({ checks: {}, storage: { used: 0, free: 0 } })
  })

  it('opens in metadata-only mode and renders fleet evidence', async () => {
    const wrapper = mount(AdminConsole)
    await flushPromises()

    expect(wrapper.text()).toContain('Metadata only')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).toContain('Safety blocks')
    expect(api.getAdminOverview).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('requires a reasoned temporary grant before rendering workspace content', async () => {
    const wrapper = mount(AdminConsole)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('Users')).trigger('click')
    await flushPromises()
    await wrapper.find('.user-row').trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Refresh tree').trigger('click')
    await flushPromises()
    await wrapper.findAll('.workspace-tree button').find(button => button.text().includes('app.py')).trigger('click')

    expect(wrapper.text()).toContain('Request 10-minute read access')
    expect(api.readAdminWorkspaceFile).not.toHaveBeenCalled()

    await wrapper.find('.guarded-preview textarea').setValue('Support ticket INC-42')
    await wrapper.find('.warning-button').trigger('click')
    await flushPromises()

    expect(api.createAdminAccessGrant).toHaveBeenCalledWith(2, 'Support ticket INC-42', 10)
    expect(api.readAdminWorkspaceFile).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Temporary content access')
    expect(wrapper.text()).toContain('print("safe")')
    wrapper.unmount()
  })
})
