<template>
  <div class="file-viewer">
    <header class="viewer-header">
      <button type="button" class="btn-back" @click="$emit('close')" title="Back to chat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M19 12H5M12 19l-7-7 7-7"/>
        </svg>
        Back
      </button>
      <div class="file-info">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
        </svg>
        <span class="file-name">{{ file?.name || 'Unknown file' }}</span>
        <span v-if="file" class="file-path">{{ file.path }}</span>
      </div>
      <button
        type="button"
        class="btn-open"
        @click="openInVSCode"
        :disabled="!file || editorBusy"
        title="Open this file in VS Code"
        aria-label="Open this file in VS Code"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/>
          <polyline points="15 3 21 3 21 9"/>
          <line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
      </button>
      <button type="button" class="btn-download" @click="downloadFile" :disabled="!file" title="Download" aria-label="Download file">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
      </button>
    </header>

    <div v-if="editorError" class="editor-notice" role="alert" aria-live="polite">
      <div class="notice-icon" aria-hidden="true">!</div>
      <div class="notice-copy">
        <strong>VS Code could not be opened</strong>
        <span>{{ editorError }}</span>
      </div>
      <div class="notice-actions">
        <button type="button" class="btn-notice-retry" :disabled="editorBusy" @click="openInVSCode">Retry</button>
        <button type="button" class="btn-notice-download" @click="downloadFile">Download</button>
        <button type="button" class="btn-notice-dismiss" aria-label="Dismiss VS Code error" @click="editorError = ''">Dismiss</button>
      </div>
    </div>

    <div v-if="file && !loading && !error && !binary" class="viewer-toolbar">
      <span class="file-stats">
        Showing {{ formatNumber(loadedLines) }} of {{ formatNumber(totalLines) }} lines
        <span aria-hidden="true">·</span>
        {{ formatSize(fileSize) }}
      </span>
      <span v-if="sourceLanguageLabel" class="language-badge">{{ sourceLanguageLabel }}</span>
      <div class="view-tabs" role="tablist" aria-label="File view mode">
        <button
          type="button"
          role="tab"
          class="view-tab"
          :class="{ active: viewMode === 'preview' }"
          :aria-selected="viewMode === 'preview'"
          @click="viewMode = 'preview'"
        >
          Preview
        </button>
        <button
          v-if="canEdit"
          type="button"
          role="tab"
          class="view-tab"
          :class="{ active: viewMode === 'edit' }"
          :aria-selected="viewMode === 'edit'"
          :disabled="preparingEdit"
          title="Edit this file"
          @click="enterEditMode"
        >
          {{ preparingEdit ? 'Preparing…' : 'Edit' }}
        </button>
      </div>
    </div>

    <div v-if="editDisabledReason && !loading && !error && !binary" class="read-only-reason" role="note">
      Read-only: {{ editDisabledReason }}
    </div>

    <div class="viewer-body" :class="{ editing: viewMode === 'edit' }">
      <div v-if="loading" class="viewer-status">Loading file...</div>
      <div v-else-if="error" class="viewer-status viewer-error">{{ error }}</div>
      <div v-else-if="binary" class="viewer-status">
        Binary file ({{ formatSize(fileSize) }})
        <button type="button" class="btn-dl" @click="downloadFile">Download</button>
      </div>
      <section v-else-if="viewMode === 'edit'" class="editor-workbench">
        <div class="editor-status-strip">
          <span :class="['save-state', dirty ? 'dirty-indicator' : 'saved-indicator']">
            <span class="dirty-dot" aria-hidden="true"></span>
            {{ dirty ? 'Unsaved changes' : 'Saved version' }}
          </span>
          <span>UTF-8</span>
          <span>{{ formatNumber(draftLineCount) }} {{ draftLineCount === 1 ? 'line' : 'lines' }}</span>
          <span class="shortcut-hint">⌘S / Ctrl+S to save</span>
        </div>

        <textarea
          ref="editorEl"
          v-model="draftContent"
          class="file-editor"
          aria-label="Edit file content"
          :disabled="saving || reloadingLatest"
          :spellcheck="false"
          @keydown="handleEditorKeydown"
        ></textarea>

        <div v-if="saveError" :class="['save-notice', { 'conflict-notice': saveConflict }]" role="alert">
          <div>
            <strong>{{ saveConflict ? 'A newer version exists' : 'Changes were not saved' }}</strong>
            <span>{{ saveError }}</span>
          </div>
          <div v-if="saveConflict" class="conflict-actions">
            <button
              type="button"
              class="btn-reload-latest"
              :disabled="reloadingLatest"
              @click="reloadLatestVersion"
            >
              {{ reloadingLatest ? 'Reloading…' : 'Reload latest' }}
            </button>
            <button
              type="button"
              class="btn-keep-editing"
              :disabled="reloadingLatest"
              @click="saveError = ''; saveConflict = false"
            >
              Keep editing
            </button>
          </div>
        </div>

        <div v-if="draftLimitError" class="save-notice draft-limit-notice" role="alert">
          <div>
            <strong>Draft is too large for browser editing</strong>
            <span>{{ draftLimitError }}</span>
          </div>
        </div>

        <footer class="editor-actions">
          <span>{{ formatNumber(draftContent.length) }} characters</span>
          <div>
            <button type="button" class="btn-cancel-edit" :disabled="saving || reloadingLatest" @click="discardChanges">
              Discard changes
            </button>
            <button
              type="button"
              class="btn-save"
              :disabled="saving || reloadingLatest || !dirty || Boolean(draftLimitError)"
              @click="saveChanges"
            >
              {{ saving ? 'Saving…' : 'Save changes' }}
            </button>
          </div>
        </footer>
      </section>
      <template v-else>
        <div v-if="!previewContent && totalLines === 0" class="viewer-status">This file is empty.</div>
        <template v-else>
          <div v-if="dirty" class="draft-preview-note" role="status">
            Previewing unsaved changes. Return to Edit to save or discard them.
          </div>
          <article v-if="isMarkdown" class="markdown-preview" v-html="renderedMarkdown"></article>
          <pre v-else class="viewer-content"><code class="source-code hljs" :class="sourceCodeClass" v-html="highlightedSource"></code></pre>
        </template>
      </template>

      <div v-if="viewMode === 'preview' && !loading && !error && !binary && (hasMore || loadMoreError)" class="pagination-footer">
        <span v-if="loadMoreError" class="load-more-error" role="alert">{{ loadMoreError }}</span>
        <button type="button" class="btn-load-more" :disabled="loadingMore || preparingEdit" @click="loadMore">
          {{ loadingMore ? 'Loading…' : `Load ${formatNumber(nextPageSize)} more lines` }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from '../utils/highlight.js'
import 'highlight.js/styles/github.css'
import * as api from '../api/client.js'

const PAGE_SIZE = 500
const EDIT_PAGE_SIZE = 5000
const MAX_EDIT_BYTES = 1024 * 1024
const MAX_EDIT_LINES = 10_000
const PROTECTED_PATH_PARTS = new Set([
  '.agent', '.agent_internal', '.agent_tmp', '.tasks', '.team', '.transcripts',
  '.git', '.vscode', '.ssh', '.aws', '.gnupg'
])
const PROTECTED_FILENAMES = new Set([
  '.env', '.netrc', '.npmrc', '.pypirc', 'credentials.json', 'id_rsa', 'id_ed25519'
])
const PROTECTED_SUFFIXES = ['.pem', '.key', '.p12', '.pfx']

const props = defineProps({
  file: { type: Object, default: null }
})
const emit = defineEmits(['close', 'saved'])

const content = ref('')
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const loadMoreError = ref('')
const editorError = ref('')
const editorBusy = ref(false)
const editorEl = ref(null)
const binary = ref(false)
const totalLines = ref(0)
const loadedLines = ref(0)
const fileSize = ref(0)
const fileSha256 = ref('')
const viewMode = ref('preview')
const draftContent = ref('')
const draftBaseline = ref('')
const originalEol = ref('\n')
const draftInitialized = ref(false)
const preparingEdit = ref(false)
const saving = ref(false)
const reloadingLatest = ref(false)
const saveError = ref('')
const saveConflict = ref(false)
let loadVersion = 0
let loadMoreOperation = 0
let editPreparationOperation = 0
let conflictReloadOperation = 0

const extensionLanguages = {
  py: 'python',
  pyw: 'python',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  vue: 'xml',
  html: 'xml',
  htm: 'xml',
  xml: 'xml',
  svg: 'xml',
  css: 'css',
  json: 'json',
  jsonl: 'json',
  md: 'markdown',
  markdown: 'markdown',
  yaml: 'yaml',
  yml: 'yaml',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  java: 'java',
  c: 'cpp',
  cc: 'cpp',
  cpp: 'cpp',
  h: 'cpp',
  hpp: 'cpp',
  go: 'go',
  rs: 'rust'
}

const fileExtension = computed(() => {
  const name = props.file?.name || props.file?.path || ''
  if (name.toLowerCase() === 'dockerfile') return 'dockerfile'
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
})

const sourceLanguage = computed(() => extensionLanguages[fileExtension.value] || (
  fileExtension.value === 'dockerfile' ? 'dockerfile' : ''
))
const sourceLanguageLabel = computed(() => {
  const labels = {
    xml: ['HTML', 'XML', 'SVG'].includes(fileExtension.value.toUpperCase())
      ? fileExtension.value.toUpperCase()
      : 'Markup',
    cpp: ['c', 'h'].includes(fileExtension.value) ? 'C' : 'C++',
    bash: 'Shell'
  }
  if (!sourceLanguage.value) return ''
  return labels[sourceLanguage.value] || sourceLanguage.value[0].toUpperCase() + sourceLanguage.value.slice(1)
})
const isMarkdown = computed(() => ['md', 'markdown'].includes(fileExtension.value))
const hasMore = computed(() => loadedLines.value < totalLines.value)
const nextPageSize = computed(() => Math.min(PAGE_SIZE, Math.max(totalLines.value - loadedLines.value, 0)))
const sourceCodeClass = computed(() => sourceLanguage.value ? `language-${sourceLanguage.value}` : '')
const dirty = computed(() => draftInitialized.value && draftContent.value !== draftBaseline.value)
const previewContent = computed(() => draftInitialized.value ? draftContent.value : content.value)
const draftLineCount = computed(() => {
  if (!draftContent.value) return 0
  const parts = draftContent.value.split(/\r\n|\r|\n/)
  return parts.length - (/\r\n$|\r$|\n$/.test(draftContent.value) ? 1 : 0)
})
const draftForSave = computed(() => restoreLineEndings(draftContent.value, originalEol.value))
const draftByteSize = computed(() => new TextEncoder().encode(draftForSave.value).byteLength)
const draftLimitError = computed(() => {
  if (!draftInitialized.value) return ''
  if (draftByteSize.value > MAX_EDIT_BYTES) {
    return `The draft is ${formatSize(draftByteSize.value)}; the browser edit limit is 1 MiB.`
  }
  if (draftLineCount.value > MAX_EDIT_LINES) {
    return `The draft has ${formatNumber(draftLineCount.value)} lines; the browser edit limit is 10,000.`
  }
  return ''
})

function detectLineEnding(value) {
  if (String(value).includes('\r\n')) return '\r\n'
  if (String(value).includes('\r')) return '\r'
  return '\n'
}

function normalizeLineEndings(value) {
  return String(value || '').replace(/\r\n|\r/g, '\n')
}

function restoreLineEndings(value, lineEnding) {
  const normalized = normalizeLineEndings(value)
  return lineEnding === '\n' ? normalized : normalized.replaceAll('\n', lineEnding)
}

function protectedPathReason(path) {
  const normalized = String(path || '').replaceAll('\\', '/').replace(/^\/+|\/+$/g, '')
  const parts = normalized.split('/').filter(Boolean).map(part => part.toLowerCase())
  if (parts.some(part => PROTECTED_PATH_PARTS.has(part))) {
    return 'Agent operational and credential directories cannot be edited here.'
  }
  const name = parts.at(-1) || ''
  if (name === '.env.example') return ''
  if (PROTECTED_FILENAMES.has(name) || name.startsWith('.env.') || PROTECTED_SUFFIXES.some(suffix => name.endsWith(suffix))) {
    return 'Credential and secret files cannot be edited in the browser.'
  }
  return ''
}

const editDisabledReason = computed(() => {
  if (!props.file?.path || binary.value) return ''
  const protectedReason = protectedPathReason(props.file.path)
  if (protectedReason) return protectedReason
  if (fileSize.value > MAX_EDIT_BYTES) return 'Files larger than 1 MiB must be edited in an IDE.'
  if (totalLines.value > MAX_EDIT_LINES) return 'Files above 10,000 lines must be edited in an IDE.'
  if (!fileSha256.value) return 'Version information is unavailable; reload the file to edit it safely.'
  return ''
})
const canEdit = computed(() => Boolean(props.file?.path) && !binary.value && !editDisabledReason.value)

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

const markdownRenderer = new marked.Renderer()
// Markdown previews deliberately show raw HTML as text. The rendered output is
// still sanitized below as a second line of defence against unsafe attributes.
markdownRenderer.html = ({ text }) => escapeHtml(text)
markdownRenderer.code = ({ text, lang }) => {
  const requested = String(lang || '').trim().split(/\s+/)[0].toLowerCase()
  const language = extensionLanguages[requested] || (hljs.getLanguage(requested) ? requested : '')
  const highlighted = language
    ? hljs.highlight(text, { language, ignoreIllegals: true }).value
    : escapeHtml(text)
  const languageClass = language ? ` language-${language}` : ''
  return `<pre><code class="hljs${languageClass}">${highlighted}</code></pre>`
}

const renderedMarkdown = computed(() => {
  if (!previewContent.value) return ''
  const raw = marked.parse(previewContent.value, {
    breaks: true,
    gfm: true,
    renderer: markdownRenderer
  })
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 's', 'del', 'code', 'pre', 'ul', 'ol', 'li',
      'a', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead',
      'tbody', 'tr', 'th', 'td', 'hr', 'span', 'input'
    ],
    // Images are intentionally omitted: previewing an untrusted Markdown file
    // must not make an implicit request to an external tracking URL.
    ALLOWED_ATTR: ['href', 'alt', 'title', 'class', 'type', 'checked', 'disabled'],
    ALLOW_DATA_ATTR: false
  })
})

const highlightedSource = computed(() => {
  if (!previewContent.value) return ''
  const highlighted = sourceLanguage.value
    ? hljs.highlight(previewContent.value, { language: sourceLanguage.value, ignoreIllegals: true }).value
    : escapeHtml(previewContent.value)
  return DOMPurify.sanitize(highlighted, {
    ALLOWED_TAGS: ['span'],
    ALLOWED_ATTR: ['class']
  })
})

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let sz = bytes
  while (sz >= 1024 && i < units.length - 1) {
    sz /= 1024
    i += 1
  }
  return `${sz.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString()
}

function applyPage(result, offset, append) {
  if (result?.binary) {
    binary.value = true
    content.value = ''
    totalLines.value = 0
    loadedLines.value = 0
    fileSize.value = Number(result?.size || props.file?.size || 0)
    return
  }

  const page = result?.content || ''
  const lineCount = Number(result?.lines || 0)
  const responseOffset = Number(result?.offset ?? offset)
  const responseLimit = Number(result?.limit || PAGE_SIZE)
  content.value = append ? content.value + page : page
  totalLines.value = lineCount
  loadedLines.value = Math.min(lineCount, responseOffset + responseLimit)
  fileSize.value = Number(result?.size || props.file?.size || 0)
  if (result?.sha256) fileSha256.value = String(result.sha256)
}

async function loadFile() {
  const version = ++loadVersion
  loadMoreOperation += 1
  editPreparationOperation += 1
  conflictReloadOperation += 1
  const path = props.file?.path
  content.value = ''
  totalLines.value = 0
  loadedLines.value = 0
  fileSize.value = Number(props.file?.size || 0)
  fileSha256.value = ''
  binary.value = false
  loading.value = false
  loadingMore.value = false
  preparingEdit.value = false
  saving.value = false
  reloadingLatest.value = false
  error.value = ''
  loadMoreError.value = ''
  editorError.value = ''
  saveError.value = ''
  saveConflict.value = false
  draftContent.value = ''
  draftBaseline.value = ''
  originalEol.value = '\n'
  draftInitialized.value = false
  viewMode.value = 'preview'

  if (!path) return

  loading.value = true
  try {
    const result = await api.readFile(path, 0, PAGE_SIZE)
    if (version !== loadVersion || path !== props.file?.path) return
    applyPage(result, 0, false)
  } catch (e) {
    if (version === loadVersion) {
      error.value = 'Failed to load file: ' + (e.message || 'Unknown error')
    }
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function loadMore() {
  if (!props.file || loadingMore.value || preparingEdit.value || !hasMore.value) return
  const operation = ++loadMoreOperation
  const version = loadVersion
  const path = props.file.path
  const offset = loadedLines.value
  loadingMore.value = true
  loadMoreError.value = ''
  try {
    const result = await api.readFile(path, offset, PAGE_SIZE)
    if (operation !== loadMoreOperation || version !== loadVersion || path !== props.file?.path) return
    if (fileSha256.value && result?.sha256 && result.sha256 !== fileSha256.value) {
      loadMoreError.value = 'The file changed while more lines were loading. Reload it before continuing.'
      return
    }
    applyPage(result, offset, true)
  } catch (e) {
    if (operation === loadMoreOperation && version === loadVersion) {
      loadMoreError.value = 'Could not load more lines: ' + (e.message || 'Unknown error')
    }
  } finally {
    if (operation === loadMoreOperation && version === loadVersion) loadingMore.value = false
  }
}

function validateSnapshotPage(result, expectedSha256) {
  if (result?.binary) throw new Error('Binary files cannot be edited in the browser.')
  const digest = String(result?.sha256 || '')
  if (!digest) throw new Error('Version information is unavailable; reload the file and try again.')
  if (expectedSha256 && digest !== expectedSha256) {
    throw new Error('The file changed while the editor was loading it. Reload the latest version and try again.')
  }
  const size = Number(result?.size || 0)
  const lines = Number(result?.lines || 0)
  if (size > MAX_EDIT_BYTES) throw new Error('The latest file is larger than the 1 MiB browser edit limit.')
  if (lines > MAX_EDIT_LINES) throw new Error('The latest file is above the 10,000-line browser edit limit.')
  return { digest, size, lines }
}

async function buildCompleteSnapshot({ reuseLoadedPage, isCurrent }) {
  const path = props.file?.path
  if (!path) return null

  let snapshotContent = reuseLoadedPage ? content.value : ''
  let snapshotLoadedLines = reuseLoadedPage ? loadedLines.value : 0
  let snapshotTotalLines = reuseLoadedPage ? totalLines.value : 0
  let snapshotSize = reuseLoadedPage ? fileSize.value : 0
  let snapshotSha256 = reuseLoadedPage ? fileSha256.value : ''
  let mustFetchFirstPage = !reuseLoadedPage

  while (mustFetchFirstPage || snapshotLoadedLines < snapshotTotalLines) {
    const offset = mustFetchFirstPage ? 0 : snapshotLoadedLines
    const result = await api.readFile(path, offset, EDIT_PAGE_SIZE)
    if (!isCurrent() || path !== props.file?.path) return null

    const page = validateSnapshotPage(result, snapshotSha256)
    const responseOffset = Number(result?.offset ?? offset)
    if (responseOffset !== offset) throw new Error('The server returned a non-contiguous file page.')

    snapshotSha256 = page.digest
    snapshotSize = page.size
    snapshotTotalLines = page.lines
    snapshotContent += String(result?.content || '')
    const responseLimit = Number(result?.limit || EDIT_PAGE_SIZE)
    const nextLoadedLines = Math.min(snapshotTotalLines, responseOffset + responseLimit)
    if (snapshotTotalLines > offset && nextLoadedLines <= offset) {
      throw new Error('The server did not return the next part of the file.')
    }
    snapshotLoadedLines = nextLoadedLines
    mustFetchFirstPage = false
  }

  if (!isCurrent() || path !== props.file?.path) return null
  return {
    content: snapshotContent,
    loadedLines: snapshotLoadedLines,
    totalLines: snapshotTotalLines,
    size: snapshotSize,
    sha256: snapshotSha256
  }
}

function commitEditorSnapshot(snapshot) {
  content.value = snapshot.content
  loadedLines.value = snapshot.loadedLines
  totalLines.value = snapshot.totalLines
  fileSize.value = snapshot.size
  fileSha256.value = snapshot.sha256
  originalEol.value = detectLineEnding(snapshot.content)
  const normalized = normalizeLineEndings(snapshot.content)
  draftContent.value = normalized
  draftBaseline.value = normalized
  draftInitialized.value = true
}

async function enterEditMode() {
  if (!canEdit.value || preparingEdit.value) return
  const operation = ++editPreparationOperation
  loadMoreOperation += 1
  loadingMore.value = false
  const version = loadVersion
  const path = props.file?.path
  saveError.value = ''
  saveConflict.value = false

  if (!draftInitialized.value) {
    preparingEdit.value = true
    try {
      const snapshot = await buildCompleteSnapshot({
        reuseLoadedPage: true,
        isCurrent: () => operation === editPreparationOperation && version === loadVersion && path === props.file?.path
      })
      if (!snapshot) return
      commitEditorSnapshot(snapshot)
    } catch (e) {
      if (operation === editPreparationOperation && version === loadVersion) {
        loadMoreError.value = e.message || 'The complete file could not be loaded for editing.'
      }
      return
    } finally {
      if (operation === editPreparationOperation && version === loadVersion) preparingEdit.value = false
    }
  }

  if (operation !== editPreparationOperation || version !== loadVersion || path !== props.file?.path) return
  viewMode.value = 'edit'
  await nextTick()
  editorEl.value?.focus()
}

function discardChanges() {
  const normalized = normalizeLineEndings(content.value)
  draftContent.value = normalized
  draftBaseline.value = normalized
  originalEol.value = detectLineEnding(content.value)
  draftInitialized.value = true
  saveError.value = ''
  saveConflict.value = false
  viewMode.value = 'preview'
}

async function saveChanges() {
  const path = props.file?.path
  const version = loadVersion
  if (!path || !dirty.value || saving.value || reloadingLatest.value || draftLimitError.value) return

  saving.value = true
  saveError.value = ''
  saveConflict.value = false
  try {
    const replacement = draftForSave.value
    const result = await api.saveFile(path, replacement, fileSha256.value)
    if (version !== loadVersion || path !== props.file?.path) return
    content.value = replacement
    draftBaseline.value = draftContent.value
    fileSha256.value = String(result.sha256 || '')
    fileSize.value = Number(result.size || 0)
    totalLines.value = Number(result.lines || 0)
    loadedLines.value = totalLines.value
    emit('saved', {
      path,
      size: fileSize.value,
      lines: totalLines.value,
      sha256: fileSha256.value,
      modified_at: result.modified_at
    })
  } catch (e) {
    if (version !== loadVersion || path !== props.file?.path) return
    saveConflict.value = e.status === 409 || e.code === 'version_conflict'
    saveError.value = saveConflict.value
      ? 'This file changed after you opened it. Reload the latest version, or keep your draft and copy it elsewhere.'
      : (e.message || 'The server rejected this save request.')
  } finally {
    if (version === loadVersion) saving.value = false
  }
}

async function reloadLatestVersion() {
  if (saving.value || reloadingLatest.value) return

  const operation = ++conflictReloadOperation
  const version = loadVersion
  const path = props.file?.path
  if (!path) return
  reloadingLatest.value = true
  try {
    const snapshot = await buildCompleteSnapshot({
      reuseLoadedPage: false,
      isCurrent: () => operation === conflictReloadOperation && version === loadVersion && path === props.file?.path
    })
    if (!snapshot) return
    if (!window.confirm('The latest version is ready. Permanently discard your unsaved draft and load it?')) return
    commitEditorSnapshot(snapshot)
    saveConflict.value = false
    saveError.value = ''
    viewMode.value = 'edit'
    await nextTick()
    editorEl.value?.focus()
  } catch (e) {
    if (operation === conflictReloadOperation && version === loadVersion && path === props.file?.path) {
      saveConflict.value = true
      saveError.value = `The latest version could not be loaded. Your draft is still intact. ${e.message || 'Please try again.'}`
    }
  } finally {
    if (operation === conflictReloadOperation && version === loadVersion) reloadingLatest.value = false
  }
}

function handleEditorKeydown(event) {
  const key = String(event.key || '').toLowerCase()
  if ((event.metaKey || event.ctrlKey) && key === 's') {
    event.preventDefault()
    saveChanges()
    return
  }
  if (event.key === 'Tab' && event.target instanceof HTMLTextAreaElement) {
    event.preventDefault()
    const start = event.target.selectionStart
    const end = event.target.selectionEnd
    event.target.setRangeText('  ', start, end, 'end')
    draftContent.value = event.target.value
  }
}

function confirmLeave() {
  if (saving.value) return false
  if (!dirty.value) return true
  return window.confirm('Discard unsaved changes to this file?')
}

function handleBeforeUnload(event) {
  if (!dirty.value && !saving.value) return
  event.preventDefault()
  event.returnValue = ''
}

async function downloadFile() {
  if (!props.file) return
  try {
    const blob = await api.downloadFile(props.file.path)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = props.file.name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    editorError.value = 'The file could not be downloaded. ' + (e.message || 'Please try again.')
  }
}

function editorFailureMessage(errorValue) {
  const message = errorValue?.message || 'Unknown editor error'
  if (message.includes('VSCODE_WEB_BASE_URL')) {
    return 'Web VS Code is not configured for this deployment. Configure VSCODE_WEB_BASE_URL, or use the built-in preview and Download.'
  }
  return `${message}. You can keep using the built-in preview or download the file.`
}

async function openInVSCode() {
  if (!props.file || editorBusy.value) return
  editorBusy.value = true
  editorError.value = ''
  try {
    const result = await api.fetchOpenUrl(props.file.path)
    const target = result?.mode === 'local-vscode'
      ? (result.file_url || result.url)
      : result?.url
    if (!target) throw new Error('The server did not return an editor URL')
    window.open(target, '_blank', 'noopener,noreferrer')
  } catch (e) {
    editorError.value = editorFailureMessage(e)
  } finally {
    editorBusy.value = false
  }
}

onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', handleBeforeUnload))
watch(() => props.file?.path, loadFile, { immediate: true })
defineExpose({ confirmLeave })
</script>

<style scoped>
.file-viewer {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  min-height: 0;
}

.viewer-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.btn-back {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 5px 10px;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: var(--font-ui);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-back:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.file-info {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  min-width: 0;
}

.file-name {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
  flex-shrink: 0;
}

.file-path {
  font-size: var(--text-sm);
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.btn-open,
.btn-download {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.btn-open:hover:not(:disabled),
.btn-download:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.btn-open:disabled,
.btn-download:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.editor-notice {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 16px 0;
  padding: 10px 12px;
  border: 1px solid #f59e0b55;
  border-radius: var(--radius-sm);
  background: #f59e0b0f;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.notice-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #f59e0b22;
  color: #c67500;
  font-weight: 700;
  flex-shrink: 0;
}

.notice-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  font-size: var(--text-sm);
  line-height: 1.45;
}

.notice-copy strong {
  color: var(--text-primary);
}

.notice-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.notice-actions button {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 5px 9px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  font: 500 var(--text-sm) var(--font-ui);
  cursor: pointer;
}

.notice-actions button:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.notice-actions button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.viewer-toolbar {
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 16px;
  border-bottom: 1px solid var(--border-light);
  background: var(--bg-secondary);
  flex-shrink: 0;
}

.file-stats {
  color: var(--text-tertiary);
  font: 500 var(--text-xs) var(--font-mono);
}

.view-tabs {
  display: flex;
  gap: 2px;
  margin-left: auto;
  padding: 2px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
}

.view-tab {
  padding: 4px 10px;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  background: transparent;
  color: var(--text-tertiary);
  font: 600 var(--text-xs) var(--font-ui);
  cursor: pointer;
}

.view-tab.active {
  background: var(--bg-primary);
  color: var(--accent);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.view-tab:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.view-tab:focus-visible,
.editor-actions button:focus-visible,
.conflict-actions button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.language-badge {
  padding: 3px 7px;
  border: 1px solid var(--border-light);
  border-radius: 999px;
  color: var(--text-tertiary);
  background: var(--bg-primary);
  font: 600 var(--text-xs) var(--font-mono);
}

.read-only-reason {
  padding: 7px 16px;
  border-bottom: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
  font-size: var(--text-xs);
  flex-shrink: 0;
}

.viewer-body {
  flex: 1;
  overflow: auto;
  min-height: 0;
}

.viewer-body.editing {
  display: flex;
  overflow: hidden;
}

.viewer-body::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.viewer-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

.viewer-content {
  min-width: fit-content;
  margin: 0;
  padding: 18px 22px;
  font-family: var(--font-mono);
  font-size: var(--text-base);
  line-height: 1.65;
  color: var(--text-primary);
  white-space: pre;
  tab-size: 4;
}

.source-code {
  display: block;
  min-height: 1em;
  padding: 0;
  background: transparent;
}

.editor-workbench {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

.editor-status-strip {
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border-light);
  background: var(--bg-secondary);
  color: var(--text-tertiary);
  font: 500 var(--text-xs) var(--font-mono);
  flex-shrink: 0;
}

.save-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #047857;
  font-family: var(--font-ui);
  font-weight: 650;
}

.dirty-indicator { color: #b45309; }

.dirty-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 3px #10b98118;
}

.dirty-indicator .dirty-dot {
  background: #f59e0b;
  box-shadow: 0 0 0 3px #f59e0b22;
}

.shortcut-hint { margin-left: auto; }

.file-editor {
  flex: 1;
  min-width: 0;
  min-height: 0;
  width: 100%;
  resize: none;
  border: 0;
  border-radius: 0;
  outline: none;
  padding: 20px 24px;
  background: var(--bg-primary);
  color: var(--text-primary);
  caret-color: var(--accent);
  font-family: var(--font-mono);
  font-size: var(--text-base);
  line-height: 1.65;
  tab-size: 2;
  white-space: pre;
  overflow: auto;
}

.file-editor:focus {
  box-shadow: inset 3px 0 0 var(--accent);
}

.file-editor:disabled { opacity: 0.72; }

.save-notice {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  padding: 10px 18px;
  border-top: 1px solid #fecaca;
  background: #fef2f2;
  color: #991b1b;
  font-size: var(--text-sm);
  flex-shrink: 0;
}

.save-notice > div:first-child {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conflict-notice {
  border-color: #fcd34d;
  background: #fffbeb;
  color: #92400e;
}

.conflict-actions,
.editor-actions > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.conflict-actions button,
.editor-actions button {
  min-height: 32px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font: 600 var(--text-sm) var(--font-ui);
  cursor: pointer;
}

.conflict-actions button:hover:not(:disabled),
.editor-actions button:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.editor-actions {
  min-height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 18px;
  border-top: 1px solid var(--border);
  background: var(--bg-secondary);
  color: var(--text-tertiary);
  font: 500 var(--text-xs) var(--font-mono);
  flex-shrink: 0;
}

.editor-actions .btn-save {
  border-color: var(--accent);
  background: var(--accent);
  color: white;
}

.editor-actions .btn-save:hover:not(:disabled) {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
  color: white;
}

.editor-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.draft-preview-note {
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 7px 16px;
  border-bottom: 1px solid #fcd34d;
  background: #fffbeb;
  color: #92400e;
  font-size: var(--text-xs);
}

.markdown-preview {
  max-width: 900px;
  margin: 0 auto;
  padding: 30px 44px 64px;
  color: var(--text-primary);
  font-family: var(--font-ui);
  font-size: var(--text-md);
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.markdown-preview :deep(h1),
.markdown-preview :deep(h2),
.markdown-preview :deep(h3),
.markdown-preview :deep(h4) {
  margin: 1.5em 0 0.65em;
  line-height: 1.28;
}

.markdown-preview :deep(h1) {
  margin-top: 0;
  padding-bottom: 0.35em;
  border-bottom: 1px solid var(--border);
  font-size: 2em;
}

.markdown-preview :deep(h2) {
  padding-bottom: 0.28em;
  border-bottom: 1px solid var(--border-light);
  font-size: 1.45em;
}

.markdown-preview :deep(h3) { font-size: 1.2em; }
.markdown-preview :deep(p) { margin: 0 0 1em; }
.markdown-preview :deep(ul),
.markdown-preview :deep(ol) { padding-left: 1.6em; }
.markdown-preview :deep(li) { margin: 0.25em 0; }

.markdown-preview :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-preview :deep(blockquote) {
  margin: 1em 0;
  padding: 0.2em 1em;
  border-left: 3px solid var(--accent-soft);
  color: var(--text-secondary);
  background: var(--bg-secondary);
}

.markdown-preview :deep(code) {
  padding: 0.15em 0.4em;
  border-radius: 4px;
  background: var(--bg-tertiary);
  font-family: var(--font-mono);
  font-size: 0.92em;
}

.markdown-preview :deep(pre) {
  margin: 1em 0;
  padding: 14px 16px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  overflow: auto;
}

.markdown-preview :deep(pre code) {
  padding: 0;
  background: transparent;
  font-size: var(--text-base);
  line-height: 1.55;
}

.markdown-preview :deep(table) {
  display: block;
  width: 100%;
  margin: 1em 0;
  border-collapse: collapse;
  overflow-x: auto;
}

.markdown-preview :deep(th),
.markdown-preview :deep(td) {
  padding: 8px 11px;
  border: 1px solid var(--border);
  text-align: left;
}

.markdown-preview :deep(th) { background: var(--bg-tertiary); }
.markdown-preview :deep(img) { max-width: 100%; }
.markdown-preview :deep(hr) { border: 0; border-top: 1px solid var(--border); }

.viewer-status {
  padding: 48px 32px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-base);
}

.viewer-error,
.load-more-error {
  color: #ef4444;
}

.pagination-footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 18px 20px 28px;
  border-top: 1px solid var(--border-light);
}

.load-more-error {
  font-size: var(--text-sm);
}

.btn-load-more,
.btn-dl {
  display: inline-block;
  padding: 7px 15px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font: 600 var(--text-sm) var(--font-ui);
  cursor: pointer;
}

.btn-load-more:hover:not(:disabled),
.btn-dl:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.btn-load-more:disabled {
  cursor: wait;
  opacity: 0.6;
}

@media (max-width: 720px) {
  .file-path { display: none; }
  .editor-notice { align-items: flex-start; flex-wrap: wrap; }
  .notice-actions { width: 100%; padding-left: 32px; }
  .markdown-preview { padding: 24px 20px 48px; }
  .viewer-toolbar { flex-wrap: wrap; }
  .view-tabs { order: 3; width: 100%; margin-left: 0; }
  .view-tab { flex: 1; }
  .shortcut-hint { display: none; }
  .file-editor { padding: 16px; }
  .save-notice,
  .editor-actions { align-items: stretch; flex-direction: column; }
  .editor-actions > div,
  .conflict-actions { width: 100%; }
  .editor-actions button,
  .conflict-actions button { flex: 1; }
}
</style>
