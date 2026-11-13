<template>
  <div class="col" style="gap: 12px">
    <div class="card toolbar">
      <nav class="crumbs">
        <button class="crumb" @click="go('')"><HardDrive /> {{ $t('serwer') }}</button>
        <template v-for="(c, i) in crumbs" :key="i">
          <ChevronRight class="sep" />
          <button class="crumb" @click="go(crumbs.slice(0, i + 1).join('/'))">{{ c }}</button>
        </template>
      </nav>
      <span class="spacer" />
      <button v-if="selected.size" class="btn sm danger" @click="removeSelected"><Trash2 /> {{ $t('Usuń ({n})', { n: selected.size }) }}</button>
      <button class="btn sm" @click="mkdir"><FolderPlus /> {{ $t('Folder') }}</button>
      <button class="btn sm" @click="newFile"><FilePlus /> {{ $t('Plik') }}</button>
      <label class="btn sm"><Upload /> {{ $t('Wgraj') }}<input type="file" multiple hidden @change="uploadFiles($event.target.files); $event.target.value = ''" /></label>
      <button class="btn sm primary" :title="$t('Pobierz wszystkie pliki serwera jako ZIP')" @click="download(`/servers/${id}/files/archive`)"><Download /> {{ $t('Pobierz wszystko') }}</button>
    </div>

    <div v-if="uploading" class="card card-body row small">
      <Loader2 class="spin" style="width: 16px" /> {{ $t('Wysyłanie {name}…', { name: uploading.name }) }}
      <div class="progress grow"><div :style="{ width: uploading.p * 100 + '%' }" /></div>
    </div>

    <div class="card files" :class="{ over: dragOver }" @dragover="onDragOver" @dragleave="onDragLeave" @drop.prevent="onDrop">
      <table class="table">
        <thead>
          <tr>
            <th style="width: 36px"><input type="checkbox" :checked="allChecked" @change="toggleAll" /></th>
            <th>{{ $t('Nazwa') }}</th><th style="width: 110px">{{ $t('Rozmiar') }}</th><th style="width: 170px">{{ $t('Zmodyfikowano') }}</th><th style="width: 130px" />
          </tr>
        </thead>
        <tbody>
          <tr v-if="path" class="clickable" @click="go(parent)"><td /><td colspan="4" class="muted"><CornerLeftUp class="fi" /> ..</td></tr>
          <tr v-for="e in entries" :key="e.name" class="clickable" @click="openEntry(e)">
            <td @click.stop><input type="checkbox" :checked="selected.has(e.name)" @change="toggle(e.name)" /></td>
            <td>
              <div class="row" style="gap: 9px">
                <Folder v-if="e.dir" class="fi dir" />
                <component :is="iconFor(e.name)" v-else class="fi" />
                <span class="ellipsis">{{ e.name }}</span>
              </div>
            </td>
            <td class="num small muted">{{ e.dir ? '—' : formatBytes(e.size) }}</td>
            <td class="small muted">{{ formatDate(e.mtime) }}</td>
            <td @click.stop>
              <div class="row acts" style="gap: 2px; justify-content: flex-end">
                <button v-if="isZip(e)" class="btn sm ghost icon" :title="$t('Rozpakuj')" @click="extract(e)"><PackageOpen /></button>
                <button class="btn sm ghost icon" :title="$t('Pobierz')" @click="download(`/servers/${id}/files/download?path=${enc(join(e.name))}`)"><Download /></button>
                <button class="btn sm ghost icon" :title="$t('Zmień nazwę')" @click="rename(e)"><PenLine /></button>
                <button class="btn sm ghost icon" :title="$t('Usuń')" @click="remove([e.name])"><Trash2 /></button>
              </div>
            </td>
          </tr>
          <tr v-if="!loading && !entries.length"><td colspan="5" class="empty small">{{ $t('Folder jest pusty. Przeciągnij tutaj pliki, aby je wgrać.') }}</td></tr>
        </tbody>
      </table>
      <div v-if="dragOver" class="dropmask"><Upload /> {{ $t('Upuść, aby wgrać do /{path}', { path }) }}</div>
    </div>

    <Modal :open="!!editor" :title="editor?.path" width="min(1100px, 96vw)" @close="closeEditor">
      <textarea v-if="editor" ref="ta" v-model="editor.content" class="editor" spellcheck="false" @keydown="editorKey" />
      <template #footer>
        <span class="tiny muted grow"><span class="kbd">{{ $t('Ctrl/⌘ S') }}</span> {{ $t('zapisuje') }}</span>
        <button class="btn" @click="closeEditor">{{ $t('Zamknij') }}</button>
        <button class="btn primary" :disabled="editor?.content === editor?.original" @click="saveFile"><Save /> {{ $t('Zapisz') }}</button>
      </template>
    </Modal>

    <Modal :open="!!prompt.open" :title="prompt.title" width="420px" @close="prompt.open = false">
      <input ref="promptInput" v-model="prompt.value" class="input mono" @keydown.enter="prompt.ok()" />
      <template #footer><button class="btn" @click="prompt.open = false">{{ $t('Anuluj') }}</button><button class="btn primary" :disabled="!prompt.value" @click="prompt.ok()">OK</button></template>
    </Modal>
  </div>
</template>
<script setup>
import { computed, inject, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { HardDrive, ChevronRight, Trash2, FolderPlus, FilePlus, Upload, Download, Loader2, CornerLeftUp, Folder, FileText, FileCode2, FileArchive, FileImage, File, Coffee, PackageOpen, PenLine, Save, Settings } from 'lucide-vue-next'
import Modal from '../../components/Modal.vue'
import { api, download, uploadWithProgress } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { formatBytes, formatDate } from '../../utils'

const live = inject('live')
const id = live.summary.id
const path = ref('')
const entries = ref([])
const loading = ref(false)
const selected = ref(new Set())
const editor = ref(null)
const ta = ref(null)
const dragOver = ref(false)
const uploading = ref(null)
const promptInput = ref(null)
const prompt = reactive({ open: false, title: '', value: '', ok: () => {} })

const enc = encodeURIComponent
const crumbs = computed(() => (path.value ? path.value.split('/') : []))
const parent = computed(() => crumbs.value.slice(0, -1).join('/'))
const join = (n) => (path.value ? `${path.value}/${n}` : n)
const allChecked = computed(() => entries.value.length && selected.value.size === entries.value.length)
const isZip = (e) => !e.dir && /\.(zip|mrpack)$/i.test(e.name)
const TEXT = /\.(txt|log|json|json5|properties|yml|yaml|toml|cfg|conf|ini|md|sh|bat|mcmeta|snbt|csv|js|xml|lang|secret)$/i
function iconFor(n) {
  if (/\.jar$/i.test(n)) return Coffee
  if (/\.(zip|gz|tar|mrpack|7z)$/i.test(n)) return FileArchive
  if (/\.(png|jpg|jpeg|gif|webp)$/i.test(n)) return FileImage
  if (/\.(properties|yml|yaml|toml|cfg|conf|ini)$/i.test(n)) return Settings
  if (/\.(json|json5|js|sh|bat|xml|snbt)$/i.test(n)) return FileCode2
  if (TEXT.test(n)) return FileText
  return File
}

async function load() {
  loading.value = true
  try {
    const r = await api.get(`/servers/${id}/files?path=${enc(path.value)}`)
    entries.value = r.entries
    selected.value = new Set()
  } catch (e) { toastError(e) } finally { loading.value = false }
}
onMounted(load)
function go(p) { path.value = p; load() }

async function openEntry(e) {
  if (e.dir) return go(join(e.name))
  if (!TEXT.test(e.name) && e.size > 512 * 1024) return download(`/servers/${id}/files/download?path=${enc(join(e.name))}`)
  try {
    const r = await api.get(`/servers/${id}/files/content?path=${enc(join(e.name))}`)
    editor.value = { path: join(e.name), content: r.content, original: r.content }
    nextTick(() => ta.value?.focus())
  } catch (err) {
    if (err.status === 415 || err.status === 413) download(`/servers/${id}/files/download?path=${enc(join(e.name))}`)
    else toastError(err)
  }
}
async function saveFile() {
  try {
    await api.put(`/servers/${id}/files/content`, { path: editor.value.path, content: editor.value.content })
    editor.value.original = editor.value.content
    toast('Zapisano plik', 'success', 1800)
    load()
  } catch (e) { toastError(e) }
}
async function closeEditor() {
  if (editor.value && editor.value.content !== editor.value.original &&
      !(await confirmDialog({ title: 'Odrzucić zmiany?', message: 'Plik ma niezapisane zmiany.', confirmText: 'Odrzuć', danger: true }))) return
  editor.value = null
}
function editorKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveFile() }
  if (e.key === 'Tab') {
    e.preventDefault()
    const el = e.target, s = el.selectionStart
    editor.value.content = editor.value.content.slice(0, s) + '  ' + editor.value.content.slice(el.selectionEnd)
    nextTick(() => { el.selectionStart = el.selectionEnd = s + 2 })
  }
}

function ask(title, value, ok) {
  Object.assign(prompt, { open: true, title, value, ok: async () => { prompt.open = false; await ok(prompt.value) } })
  nextTick(() => promptInput.value?.select())
}
const mkdir = () => ask('Nowy folder', '', async (n) => { try { await api.post(`/servers/${id}/files/mkdir`, { path: join(n) }); load() } catch (e) { toastError(e) } })
const newFile = () => ask('Nowy plik', 'nowy.txt', async (n) => { try { await api.put(`/servers/${id}/files/content`, { path: join(n), content: '' }); load() } catch (e) { toastError(e) } })
const rename = (e) => ask('Zmień nazwę', e.name, async (n) => { try { await api.post(`/servers/${id}/files/rename`, { src: join(e.name), dst: join(n) }); load() } catch (er) { toastError(er) } })

async function remove(names) {
  const label = names.length === 1 ? `„${names[0]}”` : `${names.length} elementów`
  if (!(await confirmDialog({ title: `Usunąć ${label}?`, message: 'Tej operacji nie można cofnąć (chyba że masz kopię zapasową).', confirmText: 'Usuń', danger: true }))) return
  try { await api.post(`/servers/${id}/files/delete`, { paths: names.map(join) }); load() } catch (e) { toastError(e) }
}
const removeSelected = () => remove([...selected.value])
function toggle(n) { const s = new Set(selected.value); s.has(n) ? s.delete(n) : s.add(n); selected.value = s }
function toggleAll() { selected.value = allChecked.value ? new Set() : new Set(entries.value.map((e) => e.name)) }
async function extract(e) {
  try { await api.post(`/servers/${id}/files/extract`, { path: join(e.name) }); toast('Rozpakowano', 'success'); load() } catch (er) { toastError(er) }
}

async function uploadFiles(list) {
  const files = [...list]
  if (!files.length) return
  const fd = new FormData()
  fd.append('path', path.value)
  files.forEach((f) => fd.append('files', f, f.name))
  uploading.value = { name: files.length > 1 ? `${files.length} plików` : files[0].name, p: 0 }
  try {
    await uploadWithProgress(`/servers/${id}/files/upload`, fd, (p) => (uploading.value.p = p))
    toast('Wgrano pliki', 'success', 2000)
    load()
  } catch (e) { toastError(e) } finally { uploading.value = null }
}
function onDrop(e) { endDrag(); uploadFiles(e.dataTransfer.files) }

// Drop overlay. `dragleave` also fires when moving between child elements, so it only ends the drag
// when the pointer really leaves the card. A drag cancelled with Esc or dropped outside the page
// ends without any event here, hence the window listeners and a watchdog that turns the overlay off
// once `dragover` (sent continuously while hovering) stops arriving.
let dragWatchdog
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files')
function endDrag() { clearTimeout(dragWatchdog); dragOver.value = false }
function onDragOver(e) {
  if (!hasFiles(e)) return
  e.preventDefault()
  dragOver.value = true
  clearTimeout(dragWatchdog)
  dragWatchdog = setTimeout(endDrag, 1000)
}
// Some browsers (Safari) leave relatedTarget empty even between child elements, so a leave only
// schedules the end; the next dragover over the card cancels it.
function scheduleEnd() { clearTimeout(dragWatchdog); dragWatchdog = setTimeout(endDrag, 80) }
function onDragLeave(e) { if (!e.currentTarget.contains(e.relatedTarget)) scheduleEnd() }
const onWindowDragLeave = (e) => { if (!e.relatedTarget) scheduleEnd() } // pointer left the browser window
for (const [ev, fn] of [['drop', endDrag], ['dragend', endDrag], ['dragleave', onWindowDragLeave]]) {
  onMounted(() => window.addEventListener(ev, fn))
  onUnmounted(() => window.removeEventListener(ev, fn))
}
onUnmounted(() => clearTimeout(dragWatchdog))
</script>
<style scoped>
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; flex-wrap: wrap; }
.crumbs { display: flex; align-items: center; gap: 2px; min-width: 0; flex-wrap: wrap; }
.crumb { display: inline-flex; align-items: center; gap: 6px; border: 0; background: none; color: var(--text-2); font: inherit; font-size: 13px; padding: 4px 6px; border-radius: 5px; cursor: pointer; font-family: var(--mono); }
.crumb:hover { background: var(--surface-3); color: var(--text); }
.crumb svg { width: 15px; height: 15px; }
.sep { width: 14px; height: 14px; color: var(--muted); }
.files { position: relative; overflow: hidden; }
.files.over { border-color: var(--accent); }
.clickable { cursor: pointer; }
.fi { width: 17px; height: 17px; color: var(--muted); flex: none; vertical-align: -3px; }
.fi.dir { color: var(--warn); }
.acts { opacity: 0.35; transition: opacity 0.1s; }
tr:hover .acts { opacity: 1; }
.dropmask { position: absolute; inset: 0; background: color-mix(in srgb, var(--accent) 12%, var(--surface)); display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--accent); font-weight: 600; pointer-events: none; }
.editor { width: 100%; height: min(68vh, 720px); resize: none; background: var(--console-bg); color: #dfe5e0; border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; font-family: var(--mono); font-size: 12.5px; line-height: 1.6; tab-size: 2; outline: none; }
.editor:focus { border-color: var(--accent); }
</style>
