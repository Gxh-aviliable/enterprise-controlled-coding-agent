import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FileTree from '../src/components/FileTree.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  fetchTree: vi.fn(),
  uploadFiles: vi.fn(),
  downloadWorkspace: vi.fn(),
  downloadFile: vi.fn(),
  fetchOpenUrl: vi.fn(),
  deleteItem: vi.fn(),
  moveItem: vi.fn(),
  createDir: vi.fn()
}))

vi.mock('../src/composables/useToast.js', () => ({
  useToast: () => ({ show: vi.fn(), error: vi.fn() })
}))

const fileNode = { type: 'file', name: 'README.md', path: 'README.md', size: 10 }

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const TreeNodeStub = {
  props: ['node'],
  emits: ['select', 'delete', 'rename', 'download', 'open'],
  template: `
    <div>
      <button data-test="select-node" @click="$emit('select', node)">Select</button>
      <button data-test="delete-node" @click="$emit('delete', node)">Delete</button>
      <button data-test="rename-node" @click="$emit('rename', node)">Rename</button>
    </div>
  `
}

function mountTree(props = {}) {
  return mount(FileTree, {
    props: { selectedPath: 'README.md', ...props },
    global: { stubs: { TreeNode: TreeNodeStub } }
  })
}

function assignFiles(input, files) {
  Object.defineProperty(input.element, 'files', { configurable: true, value: files })
}

describe('FileTree selected-file mutation guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchTree.mockResolvedValue({ type: 'dir', path: '', children: [fileNode] })
    api.uploadFiles.mockResolvedValue([])
    api.deleteItem.mockResolvedValue({})
    api.moveItem.mockResolvedValue({})
  })

  it('blocks an upload that would overwrite the selected file when the synchronous guard rejects it', async () => {
    const beforeMutation = vi.fn(() => false)
    const wrapper = mountTree({ beforeMutation })
    await flushPromises()
    const input = wrapper.get('input[type="file"]')
    assignFiles(input, [new File(['replacement'], 'README.md', { type: 'text/markdown' })])

    await input.trigger('change')
    await flushPromises()

    expect(beforeMutation).toHaveBeenCalledWith(expect.objectContaining({
      type: 'upload',
      affectsSelected: true,
      selectedPath: 'README.md'
    }))
    expect(api.uploadFiles).not.toHaveBeenCalled()
    expect(input.element.value).toBe('')
    wrapper.unmount()
  })

  it('does not invoke the leave guard or invalidate the viewer for an unrelated upload', async () => {
    const beforeMutation = vi.fn(() => false)
    const wrapper = mountTree({ beforeMutation })
    await flushPromises()
    const input = wrapper.get('input[type="file"]')
    const files = [new File(['new'], 'notes.md', { type: 'text/markdown' })]
    assignFiles(input, files)

    await input.trigger('change')
    await flushPromises()

    expect(beforeMutation).not.toHaveBeenCalled()
    expect(api.uploadFiles).toHaveBeenCalledWith(files, '', expect.any(Function))
    expect(wrapper.emitted('mutated')?.[0]?.[0]).toEqual(expect.objectContaining({
      type: 'upload',
      affectsSelected: false,
      selectedPath: 'README.md'
    }))
    wrapper.unmount()
  })

  it('uses the clicked folder for picker uploads and can return to the root', async () => {
    api.fetchTree.mockResolvedValue({ children: [{ type: 'dir', path: 'GXH', name: 'GXH', children: [] }] })
    const wrapper = mountTree()
    await flushPromises()
    await wrapper.get('[data-test="select-node"]').trigger('click')
    expect(wrapper.get('[aria-label="Upload directory"]').element.value).toBe('GXH')
    const files = [new File(['code'], 'checkout.py')]
    const input = wrapper.get('input[type="file"]')
    assignFiles(input, files)
    await input.trigger('change')
    await flushPromises()
    expect(api.uploadFiles).toHaveBeenCalledWith(files, 'GXH', expect.any(Function))
    expect(wrapper.emitted('mutated')[0][0].paths).toEqual(['GXH/checkout.py'])
    await wrapper.get('[title="Upload to workspace root"]').trigger('click')
    expect(wrapper.get('[aria-label="Upload directory"]').element.value).toBe('')
    wrapper.unmount()
  })

  it('guards a nested overwrite using its complete destination path', async () => {
    const beforeMutation = vi.fn(() => false)
    const wrapper = mountTree({ selectedPath: 'GXH/checkout.py', beforeMutation })
    await flushPromises()
    await wrapper.get('[aria-label="Upload directory"]').setValue('GXH')
    const input = wrapper.get('input[type="file"]')
    assignFiles(input, [new File(['replacement'], 'checkout.py')])
    await input.trigger('change')
    expect(beforeMutation).toHaveBeenCalledWith(expect.objectContaining({
      paths: ['GXH/checkout.py'], affectsSelected: true
    }))
    expect(api.uploadFiles).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses the explicit nested directory for dropped files and reports partial completion', async () => {
    const wrapper = mountTree({ selectedPath: 'GXH/src/checkout.py' })
    await flushPromises()
    await wrapper.get('[aria-label="Upload directory"]').setValue('GXH/src')
    const files = [new File(['code'], 'checkout.py'), new File(['test'], 'test_checkout.py')]
    api.uploadFiles.mockImplementationOnce(async (_files, _path, progress) => {
      progress(1)
      throw new Error('Second upload failed')
    })
    await wrapper.get('.tree-body').trigger('drop', { dataTransfer: { files } })
    await flushPromises()
    expect(api.uploadFiles).toHaveBeenCalledWith(files, 'GXH/src', expect.any(Function))
    expect(wrapper.emitted('mutated')[0][0]).toMatchObject({
      partial: true, affectsSelected: true, selectedPath: 'GXH/src/checkout.py'
    })
    wrapper.unmount()
  })

  it('uses the parent directory when selecting a file and creating a folder', async () => {
    api.fetchTree.mockResolvedValue({ children: [{ ...fileNode, path: 'GXH/README.md' }] })
    api.createDir.mockResolvedValue({})
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('examples')
    const wrapper = mountTree()
    await flushPromises()
    await wrapper.get('[data-test="select-node"]').trigger('click')
    expect(wrapper.get('[aria-label="Upload directory"]').element.value).toBe('GXH')
    await wrapper.get('[title="New Folder"]').trigger('click')
    await flushPromises()
    expect(api.createDir).toHaveBeenCalledWith('GXH/examples')
    prompt.mockRestore()
    wrapper.unmount()
  })

  it('guards deletion of the selected file before calling the API', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const beforeMutation = vi.fn(() => false)
    const wrapper = mountTree({ beforeMutation })
    await flushPromises()

    await wrapper.get('[data-test="delete-node"]').trigger('click')
    await flushPromises()

    expect(beforeMutation).toHaveBeenCalledWith(expect.objectContaining({
      type: 'delete', path: 'README.md', affectsSelected: true
    }))
    expect(api.deleteItem).not.toHaveBeenCalled()
    confirm.mockRestore()
    wrapper.unmount()
  })

  it('notifies the parent after a selected-file rename succeeds', async () => {
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('GUIDE.md')
    const beforeMutation = vi.fn(() => true)
    const wrapper = mountTree({ beforeMutation })
    await flushPromises()

    await wrapper.get('[data-test="rename-node"]').trigger('click')
    await flushPromises()

    expect(api.moveItem).toHaveBeenCalledWith('README.md', 'GUIDE.md')
    expect(wrapper.emitted('mutated')?.[0]?.[0]).toEqual(expect.objectContaining({
      type: 'rename',
      path: 'README.md',
      newPath: 'GUIDE.md',
      affectsSelected: true,
      selectedPath: 'README.md'
    }))
    prompt.mockRestore()
    wrapper.unmount()
  })

  it('reports the selection captured at mutation start when rename completion is delayed', async () => {
    const pendingMove = deferred()
    api.moveItem.mockReturnValueOnce(pendingMove.promise)
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('GUIDE.md')
    const beforeMutation = vi.fn(() => true)
    const wrapper = mountTree({ beforeMutation })
    await flushPromises()

    await wrapper.get('[data-test="rename-node"]').trigger('click')
    await wrapper.setProps({ selectedPath: 'src/app.py' })
    pendingMove.resolve({})
    await flushPromises()

    expect(wrapper.emitted('mutated')?.[0]?.[0]).toEqual(expect.objectContaining({
      type: 'rename',
      selectedPath: 'README.md',
      affectsSelected: true
    }))
    prompt.mockRestore()
    wrapper.unmount()
  })
})
