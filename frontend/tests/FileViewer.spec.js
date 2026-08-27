import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FileViewer from '../src/components/FileViewer.vue'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  readFile: vi.fn(),
  saveFile: vi.fn(),
  downloadFile: vi.fn(),
  fetchOpenUrl: vi.fn()
}))

const originalMarkdown = '# Project\n'
const originalSha256 = 'sha256-readme-v1'

const markdownFile = {
  type: 'file',
  name: 'README.md',
  path: 'docs/README.md',
  size: 256
}

const pythonFile = {
  type: 'file',
  name: 'main.py',
  path: 'src/main.py',
  size: 128
}

function mountViewer(file = markdownFile) {
  return mount(FileViewer, { props: { file } })
}

function tabByText(wrapper, label) {
  return wrapper.findAll('.view-tab').find(tab => tab.text() === label)
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

async function enterEdit(wrapper) {
  const editTab = tabByText(wrapper, 'Edit')
  expect(editTab, 'expected an Edit tab for an editable text file').toBeTruthy()
  await editTab.trigger('click')
  return wrapper.get('.file-editor')
}

describe('FileViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.readFile.mockResolvedValue({
      path: markdownFile.path,
      content: originalMarkdown,
      size: markdownFile.size,
      lines: 1,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: originalSha256,
      editable: true
    })
    api.saveFile.mockResolvedValue({
      path: markdownFile.path,
      size: 18,
      lines: 1,
      sha256: 'sha256-readme-v2'
    })
    api.downloadFile.mockResolvedValue(new Blob(['test']))
    api.fetchOpenUrl.mockResolvedValue({
      mode: 'local-vscode',
      file_url: 'vscode://file/workspaces/user_1/docs/README.md',
      url: 'http://fallback.invalid'
    })
  })

  it('renders Markdown in Preview by default and sanitizes unsafe markup', async () => {
    api.readFile.mockResolvedValueOnce({
      path: markdownFile.path,
      content: [
        '# Safe heading',
        '<script>window.__fileViewerXss = true</script>',
        '<img src=x onerror="window.__fileViewerXss = true">',
        '[unsafe link](javascript:alert(1))'
      ].join('\n'),
      size: 240,
      lines: 4,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: 'sha256-unsafe-markdown',
      editable: true
    })

    const wrapper = mountViewer()
    await flushPromises()

    const preview = wrapper.get('.markdown-preview')
    expect(preview.get('h1').text()).toBe('Safe heading')
    expect(preview.find('script').exists()).toBe(false)
    expect(preview.find('img').exists()).toBe(false)
    expect(preview.find('[onerror]').exists()).toBe(false)
    const unsafeLink = preview.find('a')
    if (unsafeLink.exists()) {
      expect(unsafeLink.attributes('href') || '').not.toMatch(/^javascript:/i)
    }
    expect(tabByText(wrapper, 'Preview').classes()).toContain('active')

    wrapper.unmount()
  })

  it('shows the Markdown source in Edit without conflating it with Preview', async () => {
    const rawMarkdown = '# Project\n\n```python\nprint("hello")\n```\n'
    api.readFile.mockResolvedValueOnce({
      path: markdownFile.path,
      content: rawMarkdown,
      size: 64,
      lines: 5,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: originalSha256,
      editable: true
    })

    const wrapper = mountViewer()
    await flushPromises()
    expect(wrapper.find('.markdown-preview').exists()).toBe(true)

    const editor = await enterEdit(wrapper)
    expect(wrapper.find('.markdown-preview').exists()).toBe(false)
    expect(editor.element.value).toContain('print("hello")')
    expect(tabByText(wrapper, 'Source')).toBeUndefined()

    await tabByText(wrapper, 'Preview').trigger('click')
    expect(wrapper.find('.markdown-preview').exists()).toBe(true)

    wrapper.unmount()
  })

  it('syntax-highlights Python source files instead of showing an unstyled pre block', async () => {
    api.readFile.mockResolvedValueOnce({
      path: pythonFile.path,
      content: 'def greet(name):\n    return f"Hello {name}"\n',
      size: pythonFile.size,
      lines: 2,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: 'sha256-python-v1',
      editable: true
    })

    const wrapper = mountViewer(pythonFile)
    await flushPromises()

    const source = wrapper.get('.source-code')
    expect(source.classes()).toContain('language-python')
    expect(source.find('.hljs-keyword').text()).toBe('def')
    expect(source.text()).toContain('greet')
    expect(wrapper.find('.markdown-preview').exists()).toBe(false)

    wrapper.unmount()
  })

  it('loads the next 500-line page and appends it without losing the first page', async () => {
    const firstPage = Array.from({ length: 500 }, (_, index) => `line-${index + 1}`).join('\n') + '\n'
    api.readFile.mockImplementation(async (path, offset, limit) => {
      if (offset === 0) {
        return {
          path,
          content: firstPage,
          size: 5000,
          lines: 501,
          offset,
          limit,
          binary: false,
          sha256: 'sha256-paged-python',
          editable: true
        }
      }
      return {
        path,
        content: 'line-501\n',
        size: 5000,
        lines: 501,
        offset,
        limit,
        binary: false,
        sha256: 'sha256-paged-python',
        editable: true
      }
    })

    const wrapper = mountViewer(pythonFile)
    await flushPromises()

    expect(api.readFile).toHaveBeenNthCalledWith(1, pythonFile.path, 0, 500)
    expect(wrapper.get('.file-stats').text()).toContain('Showing 500 of 501 lines')

    await wrapper.get('.btn-load-more').trigger('click')
    await flushPromises()

    expect(api.readFile).toHaveBeenNthCalledWith(2, pythonFile.path, 500, 500)
    expect(wrapper.get('.source-code').text()).toContain('line-1')
    expect(wrapper.get('.source-code').text()).toContain('line-501')
    expect(wrapper.get('.file-stats').text()).toContain('Showing 501 of 501 lines')
    expect(wrapper.find('.btn-load-more').exists()).toBe(false)

    wrapper.unmount()
  })

  it('does not append a newer file version to an older paginated preview', async () => {
    const firstPage = Array.from({ length: 500 }, (_, index) => `old-${index + 1}`).join('\n') + '\n'
    api.readFile
      .mockResolvedValueOnce({
        path: pythonFile.path,
        content: firstPage,
        size: 5000,
        lines: 501,
        offset: 0,
        limit: 500,
        binary: false,
        sha256: 'sha256-before-external-edit'
      })
      .mockResolvedValueOnce({
        path: pythonFile.path,
        content: 'new-501\n',
        size: 5000,
        lines: 501,
        offset: 500,
        limit: 500,
        binary: false,
        sha256: 'sha256-after-external-edit'
      })

    const wrapper = mountViewer(pythonFile)
    await flushPromises()
    await wrapper.get('.btn-load-more').trigger('click')
    await flushPromises()

    expect(wrapper.get('.load-more-error').text()).toMatch(/changed.*reload/i)
    expect(wrapper.get('.source-code').text()).not.toContain('new-501')
    expect(wrapper.get('.file-stats').text()).toContain('Showing 500 of 501 lines')

    wrapper.unmount()
  })

  it('loads every remaining page before opening the editor', async () => {
    const firstPage = Array.from({ length: 500 }, (_, index) => `line-${index + 1}`).join('\n') + '\n'
    api.readFile.mockImplementation(async (path, offset, limit) => ({
      path,
      content: offset === 0 ? firstPage : 'line-501\n',
      size: 5000,
      lines: 501,
      offset,
      limit,
      binary: false,
      sha256: 'sha256-complete-edit'
    }))

    const wrapper = mountViewer(pythonFile)
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await flushPromises()

    expect(api.readFile).toHaveBeenNthCalledWith(2, pythonFile.path, 500, 5000)
    expect(editor.element.value).toContain('line-1')
    expect(editor.element.value).toContain('line-501')
    expect(wrapper.find('.btn-load-more').exists()).toBe(false)

    wrapper.unmount()
  })

  it('does not start a second preview pagination request while one is pending', async () => {
    const firstPage = Array.from({ length: 500 }, (_, index) => `line-${index + 1}`).join('\n') + '\n'
    const pendingPage = deferred()
    api.readFile
      .mockResolvedValueOnce({
        path: pythonFile.path,
        content: firstPage,
        size: 5000,
        lines: 501,
        offset: 0,
        limit: 500,
        binary: false,
        sha256: 'sha256-no-double-page'
      })
      .mockReturnValueOnce(pendingPage.promise)

    const wrapper = mountViewer(pythonFile)
    await flushPromises()
    const loadMore = wrapper.get('.btn-load-more')
    await loadMore.trigger('click')
    await loadMore.trigger('click')

    expect(api.readFile).toHaveBeenCalledTimes(2)
    pendingPage.resolve({
      path: pythonFile.path,
      content: 'line-501\n',
      size: 5000,
      lines: 501,
      offset: 500,
      limit: 500,
      binary: false,
      sha256: 'sha256-no-double-page'
    })
    await flushPromises()
    wrapper.unmount()
  })

  it('ignores stale edit preparation without clearing or contaminating the new file operation', async () => {
    const oldPage = deferred()
    const newPage = deferred()
    const firstPage = path => Array.from({ length: 500 }, (_, index) => `${path}-${index + 1}`).join('\n') + '\n'
    api.readFile.mockImplementation((path, offset, limit) => {
      if (offset === 0) {
        return Promise.resolve({
          path,
          content: firstPage(path),
          size: 5000,
          lines: 501,
          offset,
          limit,
          binary: false,
          sha256: path === markdownFile.path ? 'sha256-old-operation' : 'sha256-new-operation'
        })
      }
      return path === markdownFile.path ? oldPage.promise : newPage.promise
    })

    const wrapper = mountViewer()
    await flushPromises()
    await tabByText(wrapper, 'Edit').trigger('click')
    expect(tabByText(wrapper, 'Preparing…')).toBeTruthy()

    await wrapper.setProps({ file: pythonFile })
    await flushPromises()
    await tabByText(wrapper, 'Edit').trigger('click')
    expect(tabByText(wrapper, 'Preparing…')).toBeTruthy()

    oldPage.resolve({
      path: markdownFile.path,
      content: 'STALE OLD PAGE\n',
      size: 5000,
      lines: 501,
      offset: 500,
      limit: 5000,
      binary: false,
      sha256: 'sha256-old-operation'
    })
    await flushPromises()
    expect(tabByText(wrapper, 'Preparing…')).toBeTruthy()
    expect(wrapper.find('.file-editor').exists()).toBe(false)

    newPage.resolve({
      path: pythonFile.path,
      content: 'NEW FINAL PAGE\n',
      size: 5000,
      lines: 501,
      offset: 500,
      limit: 5000,
      binary: false,
      sha256: 'sha256-new-operation'
    })
    await flushPromises()

    expect(wrapper.get('.file-editor').element.value).toContain('NEW FINAL PAGE')
    expect(wrapper.get('.file-editor').element.value).not.toContain('STALE OLD PAGE')
    wrapper.unmount()
  })

  it('resets pagination and view state when a different file is selected', async () => {
    api.readFile.mockImplementation(async (path, offset, limit) => {
      if (path === markdownFile.path) {
        return {
          path,
          content: '# Old file\n',
          size: 4000,
          lines: 700,
          offset,
          limit,
          binary: false,
          sha256: 'sha256-old-file',
          editable: true
        }
      }
      return {
        path,
        content: 'print("new file")\n',
        size: 24,
        lines: 1,
        offset,
        limit,
        binary: false,
        sha256: 'sha256-new-file',
        editable: true
      }
    })

    const wrapper = mountViewer()
    await flushPromises()
    expect(wrapper.find('.btn-load-more').exists()).toBe(true)

    await wrapper.setProps({ file: pythonFile })
    await flushPromises()

    expect(api.readFile).toHaveBeenLastCalledWith(pythonFile.path, 0, 500)
    expect(wrapper.get('.source-code').text()).toContain('print("new file")')
    expect(wrapper.text()).not.toContain('Old file')
    expect(wrapper.find('.btn-load-more').exists()).toBe(false)
    expect(tabByText(wrapper, 'Preview').classes()).toContain('active')

    wrapper.unmount()
  })

  it('ignores a late response from the previously selected file', async () => {
    let resolveOldFile
    let resolveNewFile
    const oldFileResponse = new Promise(resolve => { resolveOldFile = resolve })
    const newFileResponse = new Promise(resolve => { resolveNewFile = resolve })
    api.readFile.mockImplementation(path => (
      path === markdownFile.path ? oldFileResponse : newFileResponse
    ))

    const wrapper = mountViewer(markdownFile)
    await wrapper.setProps({ file: pythonFile })

    resolveNewFile({
      path: pythonFile.path,
      content: 'print("current file")\n',
      size: 32,
      lines: 1,
      offset: 0,
      limit: 500,
      binary: false
    })
    await flushPromises()
    expect(wrapper.get('.source-code').text()).toContain('current file')

    resolveOldFile({
      path: markdownFile.path,
      content: '# Stale response\n',
      size: 32,
      lines: 1,
      offset: 0,
      limit: 500,
      binary: false
    })
    await flushPromises()

    expect(wrapper.get('.source-code').text()).toContain('current file')
    expect(wrapper.text()).not.toContain('Stale response')

    wrapper.unmount()
  })

  it('shows a binary-file state without attempting to render its content', async () => {
    const binaryFile = {
      type: 'file',
      name: 'logo.png',
      path: 'assets/logo.png',
      size: 2048
    }
    api.readFile.mockResolvedValueOnce({
      path: binaryFile.path,
      content: '',
      size: binaryFile.size,
      lines: 0,
      binary: true
    })

    const wrapper = mountViewer(binaryFile)
    await flushPromises()

    expect(wrapper.text()).toContain('Binary file (2.0 KB)')
    expect(wrapper.find('.source-code').exists()).toBe(false)
    expect(wrapper.find('.markdown-preview').exists()).toBe(false)
    expect(wrapper.find('.btn-dl').exists()).toBe(true)

    wrapper.unmount()
  })

  it('opens the precise local VS Code file URL returned by the backend', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mountViewer()
    await flushPromises()

    await wrapper.get('.btn-open').trigger('click')
    await flushPromises()

    expect(api.fetchOpenUrl).toHaveBeenCalledWith(markdownFile.path)
    expect(open).toHaveBeenCalledWith(
      'vscode://file/workspaces/user_1/docs/README.md',
      '_blank',
      'noopener,noreferrer'
    )
    expect(wrapper.find('.editor-notice').exists()).toBe(false)

    open.mockRestore()
    wrapper.unmount()
  })

  it('shows VS Code configuration failures inline and lets the user dismiss them', async () => {
    api.fetchOpenUrl.mockRejectedValueOnce(new Error('VS Code integration is not configured'))
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const wrapper = mountViewer()
    await flushPromises()

    await wrapper.get('.btn-open').trigger('click')
    await flushPromises()

    const notice = wrapper.get('.editor-notice')
    expect(notice.text()).toContain('VS Code integration is not configured')
    expect(notice.find('.btn-notice-retry').exists()).toBe(true)
    expect(notice.find('.btn-notice-dismiss').exists()).toBe(true)
    expect(alert).not.toHaveBeenCalled()

    await notice.get('.btn-notice-dismiss').trigger('click')
    expect(wrapper.find('.editor-notice').exists()).toBe(false)

    alert.mockRestore()
    wrapper.unmount()
  })

  it('offers exactly Preview and Edit modes for an editable Markdown file', async () => {
    const wrapper = mountViewer()
    await flushPromises()

    expect(tabByText(wrapper, 'Preview').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('.markdown-preview').exists()).toBe(true)
    expect(wrapper.find('.file-editor').exists()).toBe(false)

    const editor = await enterEdit(wrapper)
    expect(editor.element.tagName).toBe('TEXTAREA')
    expect(editor.element.value).toBe(originalMarkdown)
    expect(wrapper.get('.editor-status-strip').text()).toContain('1 line')
    expect(tabByText(wrapper, 'Edit').attributes('aria-selected')).toBe('true')
    expect(wrapper.findAll('.view-tab').map(tab => tab.text())).toEqual(['Preview', 'Edit'])

    wrapper.unmount()
  })

  it('marks an edited buffer as unsaved and enables Save changes', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)

    expect(wrapper.get('.btn-save').attributes('disabled')).toBeDefined()
    await editor.setValue('# Updated project\n')

    expect(wrapper.get('.dirty-indicator').text()).toMatch(/unsaved/i)
    expect(wrapper.get('.btn-save').attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it('saves with the version hash that was originally read and refreshes the displayed state', async () => {
    const refreshedContent = '# Updated project\n'

    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue(refreshedContent)
    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()

    expect(api.saveFile).toHaveBeenCalledWith(
      markdownFile.path,
      refreshedContent,
      originalSha256
    )
    expect(api.readFile).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.dirty-indicator').exists()).toBe(false)
    expect(wrapper.get('.saved-indicator').text()).toMatch(/saved/i)
    await tabByText(wrapper, 'Preview').trigger('click')
    expect(wrapper.get('.markdown-preview').text()).toContain('Updated project')

    const editorAfterSave = await enterEdit(wrapper)
    await editorAfterSave.setValue('# Updated again\n')
    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()
    expect(api.saveFile).toHaveBeenLastCalledWith(
      markdownFile.path,
      '# Updated again\n',
      'sha256-readme-v2'
    )

    wrapper.unmount()
  })

  it('exposes a leave guard so the parent cannot silently switch files with an unsaved buffer', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Draft that must survive\n')

    const mayLeave = wrapper.vm.confirmLeave()

    expect(mayLeave).toBe(false)
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/unsaved/i))
    expect(api.readFile).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.file-editor').element.value).toBe('# Draft that must survive\n')
    expect(wrapper.get('.dirty-indicator').text()).toMatch(/unsaved/i)

    const beforeUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(beforeUnload)
    expect(beforeUnload.defaultPrevented).toBe(true)

    confirm.mockRestore()
    wrapper.unmount()
  })

  it.each([
    ['Ctrl+S', { key: 's', ctrlKey: true }],
    ['Cmd+S', { key: 's', metaKey: true }]
  ])('saves the current edit with %s without submitting a browser action', async (_label, keyboardInit) => {
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Keyboard save\n')

    const event = new KeyboardEvent('keydown', {
      ...keyboardInit,
      bubbles: true,
      cancelable: true
    })
    editor.element.dispatchEvent(event)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(api.saveFile).toHaveBeenCalledWith(
      markdownFile.path,
      '# Keyboard save\n',
      originalSha256
    )

    wrapper.unmount()
  })

  it('cancels an edit by restoring the last loaded content', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Throw this draft away\n')

    await wrapper.get('.btn-cancel-edit').trigger('click')

    expect(wrapper.find('.file-editor').exists()).toBe(false)
    expect(wrapper.find('.dirty-indicator').exists()).toBe(false)
    expect(wrapper.get('.markdown-preview').text()).toContain('Project')
    expect(api.saveFile).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('keeps the draft and gives actionable choices after a 409 version conflict', async () => {
    const conflict = Object.assign(
      new Error('This file changed after you opened it.'),
      { status: 409, code: 'workspace_file_conflict' }
    )
    api.saveFile.mockRejectedValueOnce(conflict)
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# My conflicting draft\n')

    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()

    const notice = wrapper.get('.conflict-notice')
    expect(notice.attributes('role')).toBe('alert')
    expect(notice.text()).toMatch(/changed|conflict/i)
    expect(notice.find('.btn-reload-latest').exists()).toBe(true)
    expect(notice.find('.btn-keep-editing').exists()).toBe(true)
    expect(wrapper.get('.file-editor').element.value).toBe('# My conflicting draft\n')
    expect(wrapper.get('.dirty-indicator').text()).toMatch(/unsaved/i)

    await notice.get('.btn-keep-editing').trigger('click')
    expect(wrapper.find('.conflict-notice').exists()).toBe(false)
    expect(wrapper.find('.file-editor').exists()).toBe(true)

    wrapper.unmount()
  })

  it('requires confirmation before committing a conflict reload and keeps the draft when reload fails', async () => {
    api.saveFile.mockRejectedValueOnce(Object.assign(new Error('Conflict'), { status: 409 }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Draft survives reload failure\n')
    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()

    await wrapper.get('.btn-reload-latest').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(api.readFile).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.file-editor').element.value).toBe('# Draft survives reload failure\n')

    api.readFile.mockRejectedValueOnce(new Error('Network unavailable'))
    await wrapper.get('.btn-reload-latest').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.file-editor').element.value).toBe('# Draft survives reload failure\n')
    expect(wrapper.get('.conflict-notice').text()).toMatch(/draft is still intact/i)
    confirm.mockRestore()
    wrapper.unmount()
  })

  it('commits a conflict reload only after the complete latest snapshot succeeds', async () => {
    api.saveFile.mockRejectedValueOnce(Object.assign(new Error('Conflict'), { status: 409 }))
    const latestTail = deferred()
    const latestFirst = Array.from({ length: 500 }, (_, index) => `latest-${index + 1}`).join('\n') + '\n'
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Keep until complete\n')
    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()

    api.readFile
      .mockResolvedValueOnce({
        path: markdownFile.path,
        content: latestFirst,
        size: 5000,
        lines: 501,
        offset: 0,
        limit: 500,
        binary: false,
        sha256: 'sha256-latest-version'
      })
      .mockReturnValueOnce(latestTail.promise)
    await wrapper.get('.btn-reload-latest').trigger('click')
    await flushPromises()

    expect(wrapper.get('.file-editor').element.value).toBe('# Keep until complete\n')
    expect(wrapper.get('.file-editor').attributes('disabled')).toBeDefined()

    latestTail.resolve({
      path: markdownFile.path,
      content: 'latest-501\n',
      size: 5000,
      lines: 501,
      offset: 500,
      limit: 5000,
      binary: false,
      sha256: 'sha256-latest-version'
    })
    await flushPromises()

    expect(wrapper.get('.file-editor').element.value).toContain('latest-1')
    expect(wrapper.get('.file-editor').element.value).toContain('latest-501')
    expect(wrapper.get('.file-editor').element.value).not.toContain('Keep until complete')
    confirm.mockRestore()
    wrapper.unmount()
  })

  it('preserves CRLF line endings when a browser-normalized draft is saved', async () => {
    api.readFile.mockResolvedValueOnce({
      path: markdownFile.path,
      content: '# Project\r\nOld line\r\n',
      size: 21,
      lines: 2,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: originalSha256
    })
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Project\nNew line\n')
    await wrapper.get('.btn-save').trigger('click')
    await flushPromises()

    expect(api.saveFile).toHaveBeenCalledWith(
      markdownFile.path,
      '# Project\r\nNew line\r\n',
      originalSha256
    )
    wrapper.unmount()
  })

  it.each([
    ['byte', 'x'.repeat(1024 * 1024 + 1), /1 MiB/i],
    ['line', `${'x\n'.repeat(10_001)}`, /10,000/i]
  ])('blocks saving as soon as the draft exceeds the %s limit', async (_kind, oversizedDraft, message) => {
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue(oversizedDraft)

    expect(wrapper.get('.draft-limit-notice').text()).toMatch(message)
    expect(wrapper.get('.btn-save').attributes('disabled')).toBeDefined()
    await wrapper.get('.btn-save').trigger('click')
    expect(api.saveFile).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not offer Edit for binary files or text files above the edit limit', async () => {
    const binaryFile = {
      type: 'file',
      name: 'archive.zip',
      path: 'artifacts/archive.zip',
      size: 2048
    }
    api.readFile.mockResolvedValueOnce({
      path: binaryFile.path,
      content: '',
      size: binaryFile.size,
      lines: 0,
      binary: true,
      editable: false,
      edit_reason: 'Binary files cannot be edited here.'
    })
    const binaryWrapper = mountViewer(binaryFile)
    await flushPromises()
    expect(tabByText(binaryWrapper, 'Edit')).toBeUndefined()
    expect(binaryWrapper.find('.file-editor').exists()).toBe(false)
    binaryWrapper.unmount()

    api.readFile.mockResolvedValueOnce({
      path: pythonFile.path,
      content: 'print("preview only")\n',
      size: 2_000_000,
      lines: 10_000,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: 'sha256-large',
      editable: false,
      edit_reason: 'Files larger than 1 MB are preview-only.'
    })
    const largeWrapper = mountViewer(pythonFile)
    await flushPromises()
    expect(tabByText(largeWrapper, 'Edit')).toBeUndefined()
    expect(largeWrapper.get('.read-only-reason').text()).toMatch(/larger than 1 (?:MiB|MB)/i)
    largeWrapper.unmount()
  })

  it('treats VS Code workspace configuration as platform-managed and read-only', async () => {
    const settingsFile = {
      type: 'file',
      name: 'settings.json',
      path: '.vscode/settings.json',
      size: 32
    }
    api.readFile.mockResolvedValueOnce({
      path: settingsFile.path,
      content: '{"editor.formatOnSave": true}\n',
      size: settingsFile.size,
      lines: 1,
      offset: 0,
      limit: 500,
      binary: false,
      sha256: 'sha256-vscode-settings'
    })

    const wrapper = mountViewer(settingsFile)
    await flushPromises()

    expect(tabByText(wrapper, 'Edit')).toBeUndefined()
    expect(wrapper.get('.read-only-reason').text()).toMatch(/operational|credential/i)
    expect(wrapper.find('.file-editor').exists()).toBe(false)
    wrapper.unmount()
  })

  it('disables edit actions while a save request is in flight', async () => {
    const pendingSave = deferred()
    api.saveFile.mockReturnValueOnce(pendingSave.promise)
    const wrapper = mountViewer()
    await flushPromises()
    const editor = await enterEdit(wrapper)
    await editor.setValue('# Saving now\n')

    await wrapper.get('.btn-save').trigger('click')

    expect(wrapper.get('.btn-save').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.btn-save').text()).toMatch(/saving/i)
    expect(wrapper.get('.btn-cancel-edit').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.file-editor').attributes('disabled')).toBeDefined()
    expect(wrapper.vm.confirmLeave()).toBe(false)
    const beforeUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(beforeUnload)
    expect(beforeUnload.defaultPrevented).toBe(true)

    pendingSave.resolve({
      path: markdownFile.path,
      size: 13,
      lines: 1,
      sha256: 'sha256-readme-v2'
    })
    await flushPromises()
    wrapper.unmount()
  })
})
