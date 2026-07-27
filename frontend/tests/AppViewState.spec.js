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

const SidebarStub = {
  emits: ['file-select', 'tab-change'],
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
  template: '<section data-test="file-viewer">File</section>'
}

describe('App main view state', () => {
  beforeEach(() => {
    auth.loggedIn = true
    auth.profile = null
    chatUnmounted.mockClear()
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
})
