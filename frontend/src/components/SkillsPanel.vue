<template>
  <section class="skills-panel" aria-labelledby="skills-title">
    <div class="skills-content">
      <header class="page-header">
        <div><h1 id="skills-title">Skills</h1><p>管理可在对话中自动使用的技能。</p></div>
        <button class="button primary" @click="beginImport()" :disabled="busy"><span aria-hidden="true">＋</span> 添加 Skill</button>
      </header>

      <div class="library-toolbar">
        <nav class="filters" aria-label="Skill 分类">
          <button v-for="tab in tabs" :key="tab.key" :aria-pressed="category === tab.key" @click="changeCategory(tab.key)" :disabled="busy">{{ tab.label }}</button>
        </nav>
        <div class="search-actions">
          <label class="search-box"><svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></svg><input v-model="query" aria-label="搜索 Skills" placeholder="搜索技能…" type="search" /></label>
          <button class="icon-button" aria-label="刷新 Skills" title="刷新" @click="refresh" :disabled="busy"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1" /></svg></button>
        </div>
      </div>
      <form v-if="category === 'personal'" class="project-scope" @submit.prevent="applyProject">
        <label for="skill-project">项目目录</label><input id="skill-project" v-model.trim="projectDraft" placeholder="可选，留空查看个人范围" :disabled="busy" /><button class="button secondary" :disabled="busy">切换范围</button>
      </form>
      <p v-if="error && !modal" class="feedback error" role="alert">{{ error }}</p>
      <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
      <details v-if="issues.length" class="discovery-issues"><summary>{{ issues.length }} 项技能暂不可用</summary><p v-for="(issue, i) in issues" :key="i">{{ issue.name || issue.path || 'Skill' }}：{{ issue.error }}</p></details>

      <div v-if="busy && !items.length && !modal" class="empty-state" role="status"><p>正在加载技能…</p></div>
      <div v-else-if="!filtered.length" class="empty-state">
        <svg aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M4 4h6a2 2 0 0 1 2 2v14a3 3 0 0 0-3-2H4zM20 4h-6a2 2 0 0 0-2 2v14a3 3 0 0 1 3-2h5z" /></svg>
        <h2>{{ emptyTitle }}</h2><p>{{ emptyDescription }}</p>
        <button v-if="query" class="button secondary" @click="query = ''">清除搜索</button>
        <button v-else-if="!project" class="button secondary" @click="beginImport()">添加 Skill</button>
      </div>
      <div v-else class="skill-grid" :aria-busy="busy">
        <button v-for="item in filtered" :key="item.id" class="skill-card" :class="{ 'is-disabled': !item.enabled }" @click="select(item, $event)" :disabled="busy">
          <span class="skill-mark" aria-hidden="true"><svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 4h12a2 2 0 0 1 2 2v14H7a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2zM4 16h15M8 4v8m3-4h4" /></svg></span>
          <span class="card-content"><span class="card-heading"><strong>{{ item.name }}</strong><span v-if="!item.enabled" class="disabled-label">已停用</span></span><span class="card-description">{{ item.description }}</span><span class="card-source">{{ sourceLabel(item) }}<span v-if="item.scope === 'project' && item.project"> · {{ item.project }}</span></span></span>
          <span class="card-arrow" aria-hidden="true">↗</span>
        </button>
      </div>
      <p v-if="filtered.length" class="library-footnote">{{ filtered.length }} 项技能<span>启用的技能会在适合的任务中自动使用。</span></p>
    </div>

    <dialog ref="dialogRef" class="skill-dialog" :class="{ 'detail-dialog': modal === 'detail' }" :aria-labelledby="modal === 'detail' ? 'skill-detail-title' : 'skill-import-title'" @cancel="cancelDialog">
      <template v-if="modal === 'detail' && detail">
        <header class="dialog-header"><span class="eyebrow">{{ sourceLabel(detail) }}</span><button class="icon-button" aria-label="关闭详情" @click="closeDialog" :disabled="busy">×</button></header>
        <div class="dialog-body">
          <h2 id="skill-detail-title">{{ detail.name }}</h2><p class="detail-description">{{ detail.description }}</p>
          <div class="detail-status"><span :class="{ 'status-off': !detail.enabled }">{{ detail.enabled ? '已启用' : '已停用' }}</span><span v-if="detail.version">版本 {{ detail.version }}</span></div>
          <div v-if="detail.editable" class="setting-list">
            <div class="setting"><div><strong>使用此技能</strong><p>停用后，新对话将不再使用。</p></div><button role="switch" :aria-checked="detail.enabled" aria-label="使用此技能" class="switch" @click="toggle('enabled')" :disabled="busy"><span /></button></div>
            <div class="setting"><div><strong>自动使用</strong><p>根据任务内容选择；关闭后可在消息中用 $名称 调用。</p></div><button role="switch" :aria-checked="detail.implicit_allowed" aria-label="自动使用" class="switch" @click="toggle('implicit_allowed')" :disabled="busy"><span /></button></div>
          </div>
          <p v-else class="managed-note">{{ detail.source === 'managed' ? '由管理员维护，可直接在对话中使用。' : '来自工作区目录，可修改原文件或导入为个人技能。' }}</p>
          <details class="detail-section"><summary>使用说明</summary><div class="skill-prose" v-html="renderInstructions(detail.content)" /></details>
          <details class="detail-section"><summary>包含的文件 <span>{{ detail.files?.length || 0 }}</span></summary><ul class="file-list"><li v-for="entry in detail.files" :key="entry.path"><code>{{ entry.path }}</code><span>{{ formatSize(entry.bytes) }}</span></li></ul></details>
          <details class="detail-section technical"><summary>技术信息</summary><dl><dt>标识</dt><dd>{{ detail.id }}</dd><dt>内容校验</dt><dd>{{ detail.sha256 }}</dd><template v-if="detail.origin?.url"><dt>来源地址</dt><dd>{{ detail.origin.url }}</dd></template></dl></details>
          <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
        </div>
        <footer v-if="detail.editable" class="dialog-footer"><button class="button danger" @click="remove" :disabled="busy">卸载</button><button class="button secondary" @click="beginImport(detail)" :disabled="busy">更新 Skill</button></footer>
      </template>
      <template v-else-if="modal === 'import'">
        <header class="dialog-header"><div><h2 id="skill-import-title">{{ replacing ? `更新 ${replacing.name}` : '添加 Skill' }}</h2><p>导入已有技能，或写下自己的工作方法。</p></div><button class="icon-button" aria-label="关闭导入" @click="closeDialog" :disabled="busy">×</button></header>
        <div class="dialog-body import-body">
          <div class="import-methods" role="group" aria-label="添加方式"><button v-for="method in methods" :key="method.key" :aria-pressed="kind === method.key" @click="kind = method.key" :disabled="busy">{{ method.label }}</button></div>
          <div v-if="kind === 'zip'" class="upload-field"><label for="skill-zip">选择 ZIP 文件</label><p>支持单个技能或包含多个技能的压缩包，最大 16 MB。</p><input id="skill-zip" type="file" accept=".zip" :disabled="busy" @change="file = $event.target.files[0]; preview = null" /></div>
          <template v-if="kind === 'git'"><label class="field">仓库地址<input v-model.trim="url" placeholder="https://github.com/组织/仓库" :disabled="busy" /></label><div class="field-row"><label class="field">分支或标签<input v-model.trim="gitRef" placeholder="HEAD" :disabled="busy" /></label><label class="field">技能子目录<input v-model.trim="subdirectory" placeholder="可选，如 skills/review" :disabled="busy" /></label></div></template>
          <label v-if="kind === 'workspace'" class="field">工作区内的目录<input v-model.trim="path" placeholder="如 my-project/skills/review" :disabled="busy" /></label>
          <template v-if="kind === 'template'"><label class="field">名称<input v-model.trim="name" placeholder="如 code-review" :disabled="busy || Boolean(replacing)" /><small>使用小写字母、数字、连字符或下划线。</small></label><label class="field">用途<textarea v-model="description" placeholder="什么时候使用这个技能？" rows="2" :disabled="busy" /></label><label class="field">操作说明<textarea v-model="guidance" placeholder="描述步骤、要求和检查方法…" rows="5" :disabled="busy" /></label></template>
          <template v-if="preview">
            <div class="preview-section"><label class="field">选择技能<select v-model="candidate" :disabled="busy"><option v-for="(item, i) in preview.candidates" :key="i" :value="i">{{ item.metadata?.name || item.path }}{{ item.valid ? '' : ' · 无法导入' }}</option></select></label>
              <template v-if="chosen"><p>{{ chosen.metadata?.description || chosen.error }}</p><p v-if="chosen.valid" class="preview-ready">已校验 · {{ chosen.files?.length || 0 }} 个文件</p><details v-if="chosen.valid" class="detail-section"><summary>查看内容</summary><div class="skill-prose" v-html="renderInstructions(chosen.content)" /><ul class="file-list"><li v-for="entry in chosen.files" :key="entry.path"><code>{{ entry.path }}</code></li></ul></details></template>
            </div>
            <label v-if="project || replacing?.project" class="field">添加到<select v-model="target" :disabled="busy || Boolean(replacing)"><option value="">个人技能</option><option :value="replacing?.project || project">项目：{{ replacing?.project || project }}</option></select></label>
          </template>
          <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
        </div>
        <footer class="dialog-footer"><span class="install-note">导入时不会运行技能中的脚本。</span><button v-if="!preview || !chosen?.valid" class="button primary" @click="discover" :disabled="busy || !canPreview">{{ busy ? '正在检查…' : '预览 Skill' }}</button><button v-else class="button primary" @click="install" :disabled="busy">{{ busy ? '正在保存…' : replacing ? '确认更新' : '确认添加' }}</button></footer>
      </template>
    </dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import * as api from '../api/client.js'

const tabs = [{ key: 'all', label: '全部' }, { key: 'public', label: '管理员发布' }, { key: 'personal', label: '用户管理' }]
const methods = [{ key: 'zip', label: 'ZIP 文件' }, { key: 'git', label: 'Git 仓库' }, { key: 'workspace', label: '工作区' }, { key: 'template', label: '自己创建' }]
const category = ref('all'), project = ref(''), projectDraft = ref(''), query = ref('')
const items = ref([]), issues = ref([]), detail = ref(null), busy = ref(false), error = ref(''), notice = ref('')
const dialogRef = ref(null), modal = ref(''), replacing = ref(null)
let returnFocus = null
const kind = ref('zip'), file = ref(null), url = ref(''), gitRef = ref('HEAD'), subdirectory = ref(''), path = ref('')
const name = ref(''), description = ref(''), guidance = ref(''), preview = ref(null), candidate = ref(0), target = ref('')
const isProject = item => item.scope === 'project'
const isPublic = item => item.source === 'managed'
const filtered = computed(() => items.value.filter(item => {
  const matches = `${item.name} ${item.description}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())
  return matches && (category.value === 'all' || (category.value === 'public' && isPublic(item)) || (category.value === 'personal' && !isPublic(item)))
}))
const chosen = computed(() => preview.value?.candidates[candidate.value])
const canPreview = computed(() => kind.value === 'zip' ? Boolean(file.value) : kind.value === 'git' ? Boolean(url.value) : kind.value === 'workspace' ? Boolean(path.value) : Boolean(name.value && description.value.trim() && guidance.value.trim()))
const emptyTitle = computed(() => query.value ? '没有找到相关技能' : category.value === 'personal' && project.value ? '这个项目还没有技能' : '还没有添加技能')
const emptyDescription = computed(() => query.value ? '换个关键词，或清除搜索后查看全部。' : category.value === 'personal' && project.value ? '输入项目目录，查看或添加仅用于该项目的技能。' : '导入一个技能，或创建适合自己工作方式的指导。')
function sourceLabel(item) { return isPublic(item) ? '管理员发布' : isProject(item) ? '用户管理 · 项目范围' : '用户管理' }
function formatSize(bytes) { return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB` }
function renderInstructions(content = '') {
  const body = content.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '').trim()
  return DOMPurify.sanitize(marked.parse(body), { FORBID_TAGS: ['img', 'iframe', 'style', 'form', 'input'], FORBID_ATTR: ['style'] })
}
async function run(fn) {
  busy.value = true; error.value = ''
  try { return await fn() } catch (e) { error.value = e.message || '操作失败，请重试。' } finally { busy.value = false }
}
async function fetchItems() { const data = await api.listSkills(project.value); items.value = data.items; issues.value = data.errors || [] }
async function refresh() { await run(fetchItems) }
async function changeCategory(next) { category.value = next; if (next !== 'personal' && project.value) { project.value = ''; await refresh() } }
async function applyProject() { project.value = projectDraft.value; await refresh() }
async function openDialog(type, opener = document.activeElement) {
  if (!dialogRef.value.open) returnFocus = opener
  modal.value = type; await nextTick(); if (!dialogRef.value.open) dialogRef.value.showModal()
}
async function closeDialog() {
  if (busy.value) return
  dialogRef.value.close(); modal.value = ''; error.value = ''
  await nextTick(); if (returnFocus?.isConnected) returnFocus.focus()
}
function cancelDialog(event) { event.preventDefault(); closeDialog() }
async function select(item, event) {
  const opener = event?.currentTarget || document.activeElement
  const result = await run(() => api.getSkill(item.id, project.value))
  if (result) { detail.value = result; await openDialog('detail', opener) }
}
async function beginImport(item = null) {
  replacing.value = item; target.value = item?.project || project.value
  kind.value = item?.origin?.kind || 'zip'; file.value = null
  url.value = item?.origin?.url || ''; gitRef.value = item?.origin?.ref || 'HEAD'; subdirectory.value = item?.origin?.subdirectory || ''; path.value = item?.origin?.path || ''
  name.value = item?.name || ''; description.value = item?.description || ''; guidance.value = (item?.content || '').replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim()
  preview.value = null; error.value = ''; notice.value = ''; await openDialog('import')
}
watch([kind, url, gitRef, subdirectory, path, name, description, guidance], () => { preview.value = null })
async function discover() {
  preview.value = null
  await run(async () => {
    preview.value = kind.value === 'zip' ? await api.previewSkillZip(file.value) : await api.previewSkillImport({ kind: kind.value, url: url.value, ref: gitRef.value || 'HEAD', subdirectory: subdirectory.value, path: path.value, name: name.value || 'new-skill', description: description.value || 'Skill', guidance: guidance.value || 'Skill' })
    candidate.value = Math.max(0, preview.value.candidates.findIndex(c => c.valid))
    if (!preview.value.candidates.length) error.value = '没有找到可导入的 Skill，请检查文件或目录。'
  })
}
async function install() {
  const updating = Boolean(replacing.value)
  const result = await run(() => api.installSkill({ preview_id: preview.value.preview_id, candidate: candidate.value, project: target.value, replace_id: replacing.value?.id, expected_version: replacing.value?.version }))
  if (result) { closeDialog(); notice.value = updating ? 'Skill 已更新，将在下一次对话中生效。' : 'Skill 已添加，将在适合的任务中自动使用。'; await refresh() }
}
async function toggle(key) {
  const item = detail.value
  await run(async () => { await api.updateSkill(item.id, { expected_version: item.version, [key]: !item[key] }); detail.value = await api.getSkill(item.id, project.value); await fetchItems() })
}
async function remove() {
  const item = detail.value
  if (!window.confirm(`卸载「${item.name}」？需要时可以重新添加。`)) return
  const result = await run(() => api.uninstallSkill(item.id, item.version))
  if (result) { closeDialog(); notice.value = 'Skill 已卸载。'; await refresh() }
}
onMounted(refresh)
</script>

<style scoped>
.skills-panel { height: 100%; overflow: auto; color: var(--text-primary); background: var(--bg-primary); }
.skills-content { max-width: 1240px; margin: 0 auto; padding: 48px clamp(24px, 4vw, 64px); }
.page-header { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 36px; }
h1 { font-family: 'Avenir Next', var(--font-ui); font-size: 36px; line-height: 1.15; letter-spacing: -1.4px; font-weight: 700; margin: 0; }
.page-header p { color: var(--text-secondary); font-size: 14px; margin: 12px 0 0; }
button, input, select, textarea { font: inherit; }
button { cursor: pointer; } button:disabled { cursor: default; opacity: .55; }
button:focus-visible, summary:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.button { display: inline-flex; justify-content: center; align-items: center; gap: 8px; min-height: 40px; padding: 9px 16px; border: 1px solid transparent; border-radius: 8px; font-size: 13px; font-weight: 600; white-space: nowrap; }
.primary { background: var(--accent); color: var(--text-inverse); } .primary:hover:not(:disabled) { background: var(--accent-hover); }
.primary > span { font-size: 19px; line-height: 1; font-weight: 400; }
.secondary { background: var(--bg-primary); border-color: var(--border); color: var(--text-primary); }
.secondary:hover:not(:disabled), .icon-button:hover:not(:disabled) { background: var(--bg-hover); }
.danger { color: #b54444; background: transparent; } .danger:hover:not(:disabled) { background: var(--bg-hover); }
.library-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
.filters { display: flex; gap: 24px; align-self: stretch; }
.filters button { padding: 16px 2px; color: var(--text-secondary); background: none; border: 0; border-bottom: 2px solid transparent; font-size: 14px; }
.filters button[aria-pressed=true] { color: var(--text-primary); border-bottom-color: var(--accent); font-weight: 600; }
.search-actions { display: flex; align-items: center; gap: 8px; padding-bottom: 8px; }
.search-box { display: flex; align-items: center; gap: 8px; background: var(--bg-secondary); border: 1px solid var(--border-light); border-radius: 8px; padding: 0 12px; color: var(--text-secondary); }
.search-box input { width: 170px; min-width: 0; background: transparent; color: var(--text-primary); border: 0; padding: 10px 0; font-size: 13px; }
.icon-button { flex: 0 0 auto; display: grid; place-items: center; width: 36px; height: 36px; border: 0; border-radius: 8px; color: var(--text-secondary); background: transparent; font-size: 26px; line-height: 1; }
.skill-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.skill-card { display: flex; position: relative; align-items: flex-start; gap: 16px; text-align: left; min-width: 0; min-height: 158px; padding: 24px; border: 1px solid var(--border); border-radius: 12px; color: inherit; background: var(--bg-primary); transition: border-color 150ms, background 150ms; }
.skill-card:hover:not(:disabled) { border-color: var(--accent-soft); background: var(--bg-secondary); }
.skill-mark { display: grid; place-items: center; flex-shrink: 0; width: 40px; height: 46px; border-radius: 4px 8px 8px 4px; border-left: 3px solid var(--accent); color: var(--accent); background: var(--accent-light); }
.card-content { min-width: 0; display: flex; flex-direction: column; gap: 9px; flex: 1; }
.card-heading { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-right: 14px; }
.card-heading strong { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.card-description { color: var(--text-secondary); font-size: 13px; line-height: 1.65; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; min-height: 43px; }
.card-source { font-size: 11px; line-height: 1.5; color: var(--text-secondary); overflow-wrap: anywhere; }
.card-arrow { position: absolute; right: 20px; top: 22px; color: var(--text-tertiary); font-size: 16px; }
.disabled-label { padding: 2px 6px; background: var(--bg-tertiary); color: var(--text-secondary); border-radius: 4px; font-size: 10px; }
.is-disabled .skill-mark { color: var(--text-secondary); background: var(--bg-tertiary); border-left-color: var(--text-tertiary); }
.library-footnote { display: flex; gap: 16px; margin: 24px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.library-footnote span { color: var(--text-secondary); }
.project-scope { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 16px; margin-bottom: 24px; border-radius: 8px; background: var(--bg-secondary); font-size: 13px; }
.project-scope input { flex: 1; min-width: 140px; }
.field input, .field select, .field textarea, .project-scope input { border: 1px solid var(--border); background: var(--bg-primary); color: var(--text-primary); border-radius: 8px; padding: 10px 12px; min-width: 0; width: 100%; box-sizing: border-box; font-size: 13px; }
.empty-state { text-align: center; padding: 72px 20px; color: var(--text-secondary); }
.empty-state svg { color: var(--accent); margin-bottom: 20px; }.empty-state h2 { color: var(--text-primary); font-size: 18px; }.empty-state p { font-size: 13px; margin: 12px 0 24px; line-height: 1.7; }
.feedback { font-size: 13px; line-height: 1.7; background: var(--bg-secondary); padding: 12px 16px; border-radius: 8px; margin: 0 0 20px; overflow-wrap: anywhere; }
.error { color: #b54444; }.discovery-issues { margin-bottom: 20px; font-size: 13px; color: var(--text-secondary); }.discovery-issues p { overflow-wrap: anywhere; }
.skill-dialog { color: var(--text-primary); background: var(--bg-primary); padding: 0; border: 1px solid var(--border); border-radius: 16px; width: min(640px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); margin: auto; overflow: auto; box-shadow: var(--shadow-lg); }
.skill-dialog::backdrop { background: rgb(15 20 40 / .3); }
.detail-dialog { border-radius: 0; margin: 0 0 0 auto; height: 100dvh; max-height: 100dvh; width: min(560px, 100vw); }
.dialog-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 24px 28px; border-bottom: 1px solid var(--border-light); }
.dialog-header h2 { font-size: 19px; line-height: 1.5; margin: 0; overflow-wrap: anywhere; }.dialog-header p { margin: 6px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.eyebrow { font-size: 12px; color: var(--text-secondary); }
.dialog-body { padding: 28px; }.dialog-body h2 { margin: 0 0 12px; font-size: 24px; overflow-wrap: anywhere; letter-spacing: -.5px; }
.detail-description { font-size: 14px; line-height: 1.8; color: var(--text-secondary); overflow-wrap: anywhere; }
.detail-status { display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary); margin: 20px 0 28px; }.detail-status > span:first-child { color: var(--accent); }.detail-status > .status-off:first-child { color: var(--text-tertiary); }
.setting-list { padding: 0 16px; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 28px; }.setting { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 0; }.setting + .setting { border-top: 1px solid var(--border-light); }.setting strong { font-size: 13px; font-weight: 500; }.setting p { font-size: 12px; line-height: 1.6; margin: 5px 0 0; color: var(--text-secondary); }
.switch { width: 34px; height: 20px; border: 0; border-radius: 12px; background: var(--text-tertiary); flex-shrink: 0; padding: 3px; }.switch span { display: block; width: 14px; height: 14px; background: #fff; border-radius: 50%; }.switch[aria-checked=true] { background: var(--accent); }.switch[aria-checked=true] span { margin-left: 14px; }
.managed-note { color: var(--text-secondary); font-size: 13px; margin-bottom: 28px; }
.detail-section { border-top: 1px solid var(--border); padding: 17px 0; }.detail-section summary { cursor: pointer; font-size: 13px; font-weight: 500; }.detail-section summary > span { font-weight: 400; color: var(--text-secondary); margin-left: 8px; }
.file-list { list-style: none; padding: 8px 0 0; margin: 0; }.file-list li { display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; font-size: 12px; }.file-list code { overflow-wrap: anywhere; font-family: var(--font-mono); }.file-list span { color: var(--text-secondary); white-space: nowrap; }
.technical { color: var(--text-secondary); }.technical dl { font-size: 11px; }.technical dt { margin: 16px 0 6px; }.technical dd { margin: 0; font-family: var(--font-mono); overflow-wrap: anywhere; }
.skill-prose { margin-top: 16px; font-size: 13px; line-height: 1.8; overflow-wrap: anywhere; }.skill-prose :deep(pre) { padding: 14px; border-radius: 6px; background: var(--bg-secondary); overflow: auto; }.skill-prose :deep(h1), .skill-prose :deep(h2), .skill-prose :deep(h3) { font-size: 16px; margin: 20px 0 10px; }.skill-prose :deep(ul), .skill-prose :deep(ol) { padding-left: 20px; }.skill-prose :deep(a) { color: var(--accent); }
.dialog-footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 20px 28px; border-top: 1px solid var(--border); background: var(--bg-primary); position: sticky; bottom: 0; }
.import-methods { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; padding: 4px; border-radius: 9px; background: var(--bg-secondary); margin-bottom: 24px; }.import-methods button { padding: 9px 4px; font-size: 12px; border: 1px solid transparent; border-radius: 6px; color: var(--text-secondary); background: transparent; }.import-methods button[aria-pressed=true] { color: var(--text-primary); background: var(--bg-primary); border-color: var(--border); }
.field { display: grid; gap: 8px; margin: 16px 0; font-size: 13px; }.field textarea { resize: vertical; line-height: 1.7; }.field small { color: var(--text-secondary); font-size: 11px; }.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.field-row .field { margin-top: 0; }
.upload-field { padding: 24px; border: 1px dashed var(--border); border-radius: 10px; background: var(--bg-secondary); }.upload-field label { font-size: 14px; font-weight: 500; }.upload-field p { font-size: 12px; line-height: 1.7; color: var(--text-secondary); margin: 8px 0 20px; }.upload-field input { width: 100%; font-size: 12px; color: var(--text-secondary); }.upload-field input::file-selector-button { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; margin-right: 12px; background: var(--bg-primary); color: var(--text-primary); cursor: pointer; }
.preview-section { border-top: 1px solid var(--border); margin-top: 24px; padding-top: 8px; }.preview-section p { font-size: 13px; line-height: 1.7; color: var(--text-secondary); }.preview-section .preview-ready { color: var(--accent); font-size: 12px; }.install-note { color: var(--text-secondary); font-size: 11px; line-height: 1.6; }
@media (max-width: 1100px) { .skills-content { padding: 32px 24px; }.skill-grid { grid-template-columns: 1fr; }.library-toolbar { flex-wrap: wrap; gap: 8px; }.search-actions { flex: 1; justify-content: flex-end; }.search-box input { width: 140px; } }
@media (max-width: 600px) { .skills-content { padding: 28px 18px; }.page-header { gap: 12px; margin-bottom: 24px; }h1 { font-size: 30px; }.page-header p { font-size: 12px; max-width: 180px; line-height: 1.6; }.filters { width: 100%; justify-content: space-between; }.search-actions { width: 100%; padding-bottom: 12px; }.search-box { flex: 1; }.search-box input { width: 100%; }.skill-card { padding: 20px 16px; gap: 12px; }.card-arrow { right: 14px; top: 18px; }.library-footnote { flex-direction: column; gap: 4px; }.dialog-header, .dialog-body { padding: 20px; }.dialog-footer { padding: 16px 20px; }.field-row { grid-template-columns: 1fr; gap: 0; }.project-scope input { flex-basis: 100%; }.install-note { max-width: 140px; } }
@media (prefers-reduced-motion: reduce) { .skill-card { transition: none; } }
</style>
