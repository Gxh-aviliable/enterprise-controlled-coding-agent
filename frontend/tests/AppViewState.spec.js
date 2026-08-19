import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App.vue'
import { auth } from '../src/stores/auth.js'
import * as api from '../src/api/client.js'

vi.mock('../src/api/client.js', () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  deleteSession: vi.fn().mockResolvedValue({}),
  getMe: vi.fn().mockResolvedValue({
    id: 1,
    username: 'tester',
    is_superuser: false,
    permissions: []
  }),
  clearTokens: vi.fn()
}))

const chatUnmounted = vi.fn()
const confirmFileLeave = vi.fn(() => true)
const setSidebarTab = vi.fn()

const SidebarStub = {
  emits: ['file-select', 'tab-change', 'logout', 'workspace-mutated'],
  setup(_, { expose }) {
    expose({ setActiveTab: setSidebarTab })
  },
  template: `
    <aside>
      <button
        data-test="open-file"
        @click="$emit('file-select', { type: 'file', name: 'README.md', path: 'README.md' })"
      >
        Open file
      </button>
      <button data-test="open-chat" @click="$emit('tab-change', 'sessions')">
        Open chat
      </button>
      <button
        data-test="open-second-file"
        @click="$emit('file-select', { type: 'file', name: 'app.py', path: 'app.py' })"
      >
        Open second file
      </button>
      <button data-test="logout" @click="$emit('logout')">Sign out</button>
      <button
        data-test="mutate-selected"
        @click="$emit('workspace-mutated', {
          type: 'delete',
          affectsSelected: true,
          selectedPath: 'README.md'
        })"
      >
        Delete selected
      </button>
      <button
        data-test="mutate-unrelated"
        @click="$emit('workspace-mutated', {
          type: 'delete',
          affectsSelected: false,
          selectedPath: 'README.md'
        })"
      >
        Delete unrelated
      </button>
    </aside>
  `
}

const ChatPanelStub = {
  props: ['sessionId'],
  data: () => ({ draft: '' }),
  unmounted: chatUnmounted,
  template: `
    <section data-test="chat-panel">
      <input data-test="chat-draft" v-model="draft" />
    </section>
  `
}

const FileViewerStub = {
  props: ['file'],
  setup(_, { expose }) {
    expose({ confirmLeave: confirmFileLeave })
  },
  template: '<section data-test="file-viewer">{{ file.path }}</section>'
}

describe('App main view state', () => {
  beforeEach(() => {
    auth.loggedIn = true
    auth.profile = null
    chatUnmounted.mockClear()
    confirmFileLeave.mockReset()
    confirmFileLeave.mockReturnValue(true)
    setSidebarTab.mockClear()
    api.clearTokens.mockClear()
    api.listSessions.mockResolvedValue([])
  })

  afterEach(() => {
    auth.loggedIn = false
    auth.profile = null
  })

  it('keeps the chat component and its local state across file navigation', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()

    await wrapper.get('[data-test="chat-draft"]').setValue('unfinished message')
    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="file-viewer"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="chat-panel"]').element.style.display).toBe('none')
    expect(chatUnmounted).not.toHaveBeenCalled()

    await wrapper.get('[data-test="open-chat"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="file-viewer"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="chat-panel"]').element.style.display).toBe('')
    expect(wrapper.get('[data-test="chat-draft"]').element.value).toBe('unfinished message')
    expect(chatUnmounted).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('keeps an unsaved file open when the user rejects navigation', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()

    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()
    confirmFileLeave.mockReturnValue(false)

    await wrapper.get('[data-test="open-chat"]').trigger('click')
    await flushPromises()

    expect(confirmFileLeave).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-test="file-viewer"]').text()).toBe('README.md')
    expect(wrapper.get('[data-test="chat-panel"]').element.style.display).toBe('none')
    expect(setSidebarTab).toHaveBeenCalledWith('files')

    await wrapper.get('[data-test="open-second-file"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="file-viewer"]').text()).toBe('README.md')

    wrapper.unmount()
  })

  it('routes logout through the file leave guard', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()
    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()

    confirmFileLeave.mockReturnValue(false)
    await wrapper.get('[data-test="logout"]').trigger('click')
    expect(auth.loggedIn).toBe(true)
    expect(wrapper.find('[data-test="file-viewer"]').exists()).toBe(true)

    confirmFileLeave.mockReturnValue(true)
    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()
    expect(auth.loggedIn).toBe(false)
    expect(api.clearTokens).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('closes only the viewer invalidated by a successful workspace mutation', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()
    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-test="mutate-unrelated"]').trigger('click')
    expect(wrapper.get('[data-test="file-viewer"]').text()).toBe('README.md')

    await wrapper.get('[data-test="mutate-selected"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="file-viewer"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="chat-panel"]').element.style.display).toBe('')
    wrapper.unmount()
  })

  it('rechecks unsaved state when a pending mutation completes and preserves a newly-created draft', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()
    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()

    confirmFileLeave.mockReturnValue(false)
    await wrapper.get('[data-test="mutate-selected"]').trigger('click')
    await flushPromises()

    expect(confirmFileLeave).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-test="file-viewer"]').text()).toBe('README.md')
    expect(wrapper.get('[data-test="chat-panel"]').element.style.display).toBe('none')
    expect(setSidebarTab).toHaveBeenCalledWith('files')
    wrapper.unmount()
  })

  it('ignores a delayed mutation event captured for a previously selected file', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Sidebar: SidebarStub,
          ChatPanel: ChatPanelStub,
          FileViewer: FileViewerStub,
          MemoryViewer: true,
          TraceViewer: true,
          Toast: true
        }
      }
    })
    await flushPromises()
    await wrapper.get('[data-test="open-file"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="open-second-file"]').trigger('click')
    await flushPromises()

    confirmFileLeave.mockClear()
    await wrapper.get('[data-test="mutate-selected"]').trigger('click')
    await flushPromises()

    expect(confirmFileLeave).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="file-viewer"]').text()).toBe('app.py')
    wrapper.unmount()
  })
})
