<template>
  <div v-if="d" class="col" style="gap: 16px">
    <div class="small muted">{{ $t('Datapacki zmieniają rozgrywkę bez modów: przepisy, łupy, struktury, biomy i generowanie świata. Działają na każdym silniku i są wczytywane ze świata') }} <span class="mono">{{ d.level }}/datapacks/</span>.</div>
    <div v-if="restartNeeded" class="callout warn small"><RotateCcw /><div class="grow">{{ $t('Zmiany w datapackach zadziałają po restarcie serwera.') }}</div><button class="btn sm" @click="restart">{{ $t('Restartuj teraz') }}</button></div>

    <section class="card">
      <div class="card-head"><PackageOpen class="hi" /><h3>{{ $t('Datapacki na serwerze') }}</h3><span class="badge">{{ d.packs.length }}</span>
        <label class="btn sm"><Upload /> {{ $t('Wgraj ZIP') }}<input type="file" accept=".zip" hidden @change="upload($event.target.files[0]); $event.target.value = ''" /></label></div>
      <div class="drop" :class="{ over: dragOver }" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="dragOver = false; upload($event.dataTransfer.files[0])">
        <div v-if="uploading" class="row small" style="width: 100%"><Loader2 class="spin" style="width: 16px" /> {{ $t('Wysyłanie…') }} <div class="progress grow"><div :style="{ width: uploading * 100 + '%' }" /></div></div>
        <div v-else-if="!d.packs.length" class="muted small"><Upload class="ui" /> {{ $t('Przeciągnij tutaj plik .zip z datapackiem') }}</div>
        <div v-else class="packs">
          <div v-for="p in d.packs" :key="p.file" class="pack" :class="{ off: !p.enabled }">
            <img v-if="iconOf(p)" :src="iconOf(p)" class="pic" alt="" referrerpolicy="no-referrer" @error="(e) => (e.target.style.visibility = 'hidden')" />
            <div v-else class="pic ph"><PackageOpen /></div>
            <div class="grow" style="min-width: 0">
              <div class="row" style="gap: 6px"><b class="ellipsis">{{ p.name }}</b><span v-if="!p.enabled" class="badge">{{ $t('wyłączony') }}</span></div>
              <div class="tiny muted ellipsis">{{ p.description || p.file }}</div>
              <div class="row wrap tiny" style="gap: 6px; margin-top: 4px">
                <span class="badge">{{ $t(SOURCE[p.source] || p.source) }}</span>
                <span v-if="p.version" class="badge mono">{{ p.version }}</span>
                <span v-if="p.folder" class="badge">{{ $t('folder') }}</span>
                <span v-if="p.pack_format" class="badge" :title="'pack_format w pack.mcmeta'">{{ $t('format {n}', { n: p.pack_format }) }}</span>
                <span class="badge">{{ formatBytes(p.size) }}</span>
              </div>
            </div>
            <Toggle :model-value="p.enabled" :disabled="busy[p.file]" :title="$t(p.enabled ? 'Wyłącz' : 'Włącz')" @update:model-value="toggle(p, $event)" />
            <button class="btn sm ghost icon" :title="$t('Usuń')" :disabled="busy[p.file]" @click="remove(p)"><Trash2 /></button>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><Search class="hi" /><h3>{{ $t('Pobierz z Modrinth') }}</h3></div>
      <div class="card-body col" style="gap: 12px">
        <div class="row wrap" style="gap: 10px">
          <div class="searchbox grow"><Search /><input v-model="q" :placeholder="$t('Szukaj datapacków, np. Terralith, Vanilla Tweaks…')" @keydown.enter="search(true)" /></div>
          <Toggle v-model="anyVersion" :label="$t('Wszystkie wersje')" @update:model-value="search(true)" />
        </div>
        <div v-if="error" class="callout warn small"><AlertTriangle />{{ $tm(error) }}</div>
        <div v-if="searching && !results.length" class="empty"><Loader2 class="spin" /></div>
        <div v-else-if="!searching && !results.length" class="small muted">{{ $t('Nic nie znaleziono dla tej wersji Minecrafta.') }}</div>
        <div class="results">
          <div v-for="r in results" :key="r.id" class="res">
            <img v-if="r.icon" :src="r.icon" class="pic" alt="" referrerpolicy="no-referrer" loading="lazy" />
            <div v-else class="pic" />
            <div class="grow" style="min-width: 0">
              <a :href="r.url" target="_blank" rel="noopener" class="title ellipsis">{{ r.title }}</a>
              <div class="tiny muted desc">{{ r.description }}</div>
              <span class="badge" style="margin-top: 6px"><Download class="bi" /> {{ compact(r.downloads) }}</span>
            </div>
            <button class="btn sm" :disabled="busy[r.id] || installed.has(r.id)" @click="install(r)">
              <Loader2 v-if="busy[r.id]" class="spin" /><Check v-else-if="installed.has(r.id)" /><Download v-else />
              {{ $t(installed.has(r.id) ? 'Zainstalowany' : 'Zainstaluj') }}</button>
          </div>
        </div>
        <div v-if="results.length && results.length < total" class="row" style="justify-content: center">
          <button class="btn sm" :disabled="searching" @click="search(false)">{{ $t('Pokaż więcej') }}</button>
        </div>
      </div>
    </section>
  </div>
  <div v-else class="empty"><Loader2 class="spin" /></div>
</template>
<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { AlertTriangle, RotateCcw, PackageOpen, Upload, Loader2, Check, Download, Trash2, Search } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import { api, uploadWithProgress } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { compact, formatBytes } from '../../utils'

const live = inject('live')
const id = live.summary.id
const SOURCE = { upload: 'wgrany', modrinth: 'Modrinth' }
const d = ref(null)
const restartNeeded = ref(false)
const dragOver = ref(false)
const uploading = ref(null)
const busy = reactive({})
const q = ref('')
const anyVersion = ref(false)
const results = ref([])
const total = ref(0)
const searching = ref(false)
const error = ref('')
const installed = computed(() => new Set((d.value?.packs || []).map((p) => p.project_id).filter(Boolean)))
const iconOf = (p) => (p.has_icon ? `/api/servers/${id}/datapacks/${encodeURIComponent(p.file)}/icon` : p.icon_url || '')

async function load() {
  try { d.value = await api.get(`/servers/${id}/datapacks`) } catch (e) { toastError(e) }
}
onMounted(async () => { await load(); search(true) })

function changed(r) { if (r?.restart_required) restartNeeded.value = true }

async function upload(file) {
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  uploading.value = 0.01
  try {
    const p = await uploadWithProgress(`/servers/${id}/datapacks/upload`, fd, (v) => (uploading.value = v))
    toast(`Dodano datapack „${p.name}”${p.fixed ? ' (poprawiono strukturę ZIP)' : ''}`, 'success')
    changed(p)
    load()
  } catch (e) { toastError(e) } finally { uploading.value = null }
}
async function toggle(p, on) {
  busy[p.file] = true
  try { changed(await api.post(`/servers/${id}/datapacks/toggle`, { file: p.file, enabled: on })); await load() } catch (e) { toastError(e) } finally { busy[p.file] = false }
}
async function remove(p) {
  if (!(await confirmDialog({ title: `Usunąć „${p.name}”?`, message: 'Zmiany, które datapack wprowadził w już wygenerowanym świecie, zostaną.', confirmText: 'Usuń', danger: true }))) return
  busy[p.file] = true
  try { changed(await api.del(`/servers/${id}/datapacks/${encodeURIComponent(p.file)}`)); await load() } catch (e) { toastError(e) } finally { busy[p.file] = false }
}
async function search(reset) {
  searching.value = true
  error.value = ''
  try {
    const off = reset ? 0 : results.value.length
    const r = await api.get(`/servers/${id}/datapacks/search?q=${encodeURIComponent(q.value)}&offset=${off}&any_version=${anyVersion.value}`)
    results.value = reset ? r.items : [...results.value, ...r.items]
    total.value = r.total
  } catch (e) { error.value = e.message } finally { searching.value = false }
}
async function install(r) {
  busy[r.id] = true
  try {
    const p = await api.post(`/servers/${id}/datapacks/install`, { project_id: r.id })
    toast(`Zainstalowano „${p.name}”`, 'success')
    changed(p)
    load()
  } catch (e) { toastError(e) } finally { busy[r.id] = false }
}
async function restart() {
  try { await api.post(`/servers/${id}/power`, { action: 'restart' }); restartNeeded.value = false } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.pic { width: 44px; height: 44px; border-radius: 8px; image-rendering: pixelated; background: var(--surface-3); flex: none; object-fit: cover; }
.pic.ph { display: grid; place-items: center; color: var(--muted); }
.pic.ph svg { width: 20px; height: 20px; }
.drop { padding: 8px; min-height: 80px; display: flex; align-items: center; justify-content: center; }
.drop.over { background: var(--accent-soft); outline: 1.5px dashed var(--accent); outline-offset: -6px; }
.ui { width: 16px; height: 16px; vertical-align: -3px; }
.packs { display: flex; flex-direction: column; width: 100%; }
.pack { display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: var(--radius-sm); }
.pack:hover { background: var(--surface-2); }
.pack.off .pic, .pack.off b { opacity: 0.55; }
.searchbox { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 12px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); min-width: 200px; }
.searchbox svg { width: 16px; height: 16px; color: var(--muted); }
.searchbox input { flex: 1; background: none; border: 0; outline: none; color: var(--text); font: inherit; }
.results { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 8px; }
.res { display: flex; align-items: center; gap: 12px; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); }
.title { color: var(--text); font-weight: 600; display: block; }
.desc { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.bi { width: 12px; height: 12px; }
@media (max-width: 600px) { .results { grid-template-columns: 1fr; } }
</style>
