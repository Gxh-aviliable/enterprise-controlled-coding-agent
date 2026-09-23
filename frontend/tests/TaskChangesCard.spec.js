import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import TaskChangesCard from '../src/components/TaskChangesCard.vue'
import * as api from '../src/api/client.js'
vi.mock('../src/api/client.js', () => ({ getTaskChanges: vi.fn(), restoreTaskFiles: vi.fn() }))
beforeEach(() => {
  vi.resetAllMocks()
  api.getTaskChanges.mockResolvedValue({ version: 'snapshot-version', files: [
    { path: 'a.txt', operation: 'modified', restorable: true, supported: true, diff: '-old\n+new' },
    { path: 'b.txt', operation: 'deleted', restorable: false, conflict: true, diff: null }
  ], validations: [], pending_execution: false, restore: null })
})
it('shows escaped diff and explicitly confirms only supported selected files', async () => {
  const wrapper = mount(TaskChangesCard, { props: { traceId: 'trace-one' } })
  expect(api.getTaskChanges).not.toHaveBeenCalled()
  await wrapper.get('.changes-toggle').trigger('click'); await flushPromises()
  expect(api.getTaskChanges).toHaveBeenCalledWith('trace-one')
  expect(wrapper.get('pre').text()).toContain('-old')
  const boxes = wrapper.findAll('input')
  expect(boxes[1].element.disabled).toBe(true)
  await boxes[0].setValue(true)
  await wrapper.findAll('button')[1].trigger('click')
  expect(api.restoreTaskFiles).not.toHaveBeenCalled()
  api.restoreTaskFiles.mockRejectedValueOnce(new Error('No files restored; content changed'))
  await wrapper.findAll('button').find(b => b.text() === 'Restore selected files').trigger('click')
  await flushPromises()
  expect(api.restoreTaskFiles).toHaveBeenCalledWith('trace-one', ['a.txt'], 'snapshot-version')
  expect(wrapper.get('[role=alert]').text()).toContain('content changed')
  expect(wrapper.text()).toContain('Network and database effects cannot be restored')
})

it('colors unified diff lines while preserving whitespace and rendering HTML as text', async () => {
  const diff = '--- before/a.txt\n+++ after/a.txt\n@@ -1,2 +1,2 @@\n unchanged\n-<script>alert(1)</script>\n+<img src=x onerror=alert(1)>\n--- deleted text\n+++ added text\n\n'
  api.getTaskChanges.mockResolvedValueOnce({ files: [{ path: 'a.txt', supported: true, diff }], validations: [] })
  const wrapper = mount(TaskChangesCard, { props: { traceId: 'color-check' } })
  await wrapper.get('.changes-toggle').trigger('click')
  await flushPromises()
  const pre = wrapper.get('pre')
  expect(pre.element.textContent).toBe(diff)
  expect(pre.findAll('.diff-meta')).toHaveLength(2)
  expect(pre.findAll('.diff-hunk')).toHaveLength(1)
  expect(pre.findAll('.diff-removed')).toHaveLength(2)
  expect(pre.findAll('.diff-added')).toHaveLength(2)
  expect(pre.find('script').exists()).toBe(false)
  expect(pre.find('img').exists()).toBe(false)
  wrapper.unmount()
})
