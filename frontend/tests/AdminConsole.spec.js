import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AdminConsole from '../src/components/admin/AdminConsole.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  previewSkillZip: vi.fn(),
  getSkillImportCandidate: vi.fn(),
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
  it('opens the new draft editor and carries a complete imported package into publication', async () => {
    const content = '---\nname: zip-demo\ndescription: Full package\n---\nUse the reference.'
    const packageFiles = { 'SKILL.md': 'c2tpbGw=', 'references/guide.md': 'cmVm', 'scripts/check.py': 'cHJpbnQoMSk=' }
    api.previewSkillZip.mockResolvedValue({ preview_id: 'preview-1', candidates: [{ valid: true, metadata: { name: 'zip-demo' }, path: 'zip-demo' }] })
    api.getSkillImportCandidate.mockResolvedValue({ valid: true, metadata: { name: 'zip-demo', description: 'Full package' }, content, package: packageFiles })
    api.saveAdminSkillDraft.mockResolvedValue({ status: 'draft', revision: 1, updated_at: '2026-09-23T00:00:00', validation: { valid: true } })
    api.publishAdminSkill.mockResolvedValue({ name: 'zip-demo', version: 1 })
    const wrapper = mount(AdminConsole)
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text().includes('公共 Skills')).trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '新建公共 Skill').trigger('click')
    expect(wrapper.find('.skill-editor textarea').exists()).toBe(true)
    const upload = wrapper.find('input[type=file]')
    Object.defineProperty(upload.element, 'files', { value: [new File(['zip'], 'skill.zip')] })
    await upload.trigger('change'); await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '预览候选到草稿编辑器').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('references/guide.md')
    await wrapper.findAll('button').find(b => b.text() === '保存草稿').trigger('click')
    await flushPromises()
    expect(api.saveAdminSkillDraft).toHaveBeenCalledWith(expect.objectContaining({ name: 'zip-demo', content, package: packageFiles }))
    await wrapper.find('input[placeholder="说明本次修改"]').setValue('Publish entire package')
    await wrapper.findAll('button').find(b => b.text() === '发布版本').trigger('click')
    await flushPromises()
    expect(api.publishAdminSkill).toHaveBeenCalledWith('zip-demo', 'Publish entire package', '2026-09-23T00:00:00', 1)
    wrapper.unmount()
  })

  it('loads an existing public skill body and allows editing, publication and retirement', async () => {
    const content = '---\nname: python\ndescription: Python guidance\n---\nOriginal guidance.'
    const detail = { name: 'python', description: 'Python guidance', draft_content: content, status: 'published', active_version: 1, revision: 1, versions: [] }
    api.listAdminSkills.mockResolvedValue({ items: [{ name: 'python', source: 'managed', status: 'published' }] })
    api.getAdminSkill.mockResolvedValue(detail)
    api.saveAdminSkillDraft.mockResolvedValue({ status: 'published', revision: 2, updated_at: '2026-09-23T01:00:00', validation: { valid: true } })
    api.publishAdminSkill.mockResolvedValue({ name: 'python', version: 2 })
    api.retireAdminSkill.mockResolvedValue({ name: 'python', status: 'retired' })
    const wrapper = mount(AdminConsole)
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text().includes('公共 Skills')).trigger('click')
    await flushPromises()
    expect(api.getAdminSkill).toHaveBeenCalledWith('python')
    const editor = wrapper.find('.skill-editor textarea')
    expect(editor.element.value).toBe(content)
    expect(editor.element.disabled).toBe(false)
    expect(wrapper.text()).not.toContain('BUILTIN')
    const edited = content.replace('Original', 'Updated')
    await editor.setValue(edited)
    await wrapper.findAll('button').find(b => b.text() === '保存草稿').trigger('click')
    await flushPromises()
    expect(api.saveAdminSkillDraft).toHaveBeenCalledWith(expect.objectContaining({ name: 'python', content: edited, expected_revision: 1 }))
    await wrapper.find('input[placeholder="说明本次修改"]').setValue('Update Python guidance')
    await wrapper.findAll('button').find(b => b.text() === '发布版本').trigger('click')
    await flushPromises()
    expect(api.publishAdminSkill).toHaveBeenCalledWith('python', 'Update Python guidance', '2026-09-23T01:00:00', 2)
    await wrapper.find('input[placeholder="说明本次修改"]').setValue('No longer needed')
    await wrapper.findAll('button').find(b => b.text() === '下架').trigger('click')
    await flushPromises()
    expect(api.retireAdminSkill).toHaveBeenCalledWith('python', 'No longer needed')
    wrapper.unmount()
  })

})
