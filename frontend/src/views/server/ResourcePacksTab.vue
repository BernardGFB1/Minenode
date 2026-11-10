<template>
  <div v-if="d" class="col" style="gap: 16px">
    <div v-if="!d.base_url" class="callout warn">
      <AlertTriangle />
      <div>{{ $t('Paczki są wysyłane graczom przez port Minecrafta z osobnej subdomeny, ale panel nie zna jeszcze swojego adresu. Ustaw') }} <b>{{ $t('domenę bazową') }}</b> {{ $t('lub') }} <b>{{ $t('publiczny adres') }}</b> w <router-link to="/settings">{{ $t('Ustawieniach panelu') }}</router-link>.</div>
    </div>
    <div v-else-if="!d.explicit_base" class="callout info small">
      <Info /><div>{{ $t('Paczki są wysyłane przez port Minecrafta pod adresem') }} <span class="mono">{{ d.base_url }}</span> {{ $t('– nie trzeba otwierać dodatkowego portu.') }}</div>
    </div>
    <div v-if="restartNeeded" class="callout warn small"><RotateCcw /><div class="grow">{{ $t('Zmiana paczki zadziała po restarcie serwera.') }}</div><button class="btn sm" @click="restart">{{ $t('Restartuj teraz') }}</button></div>

    <!-- active -->
    <section class="card">
      <div class="card-head"><Sparkles class="hi" /><h3>{{ $t('Aktywna paczka serwera') }}</h3>
        <button v-if="active" class="btn sm danger" @click="deactivate"><PowerOff /> {{ $t('Wyłącz paczkę') }}</button></div>
      <div v-if="active" class="card-body col" style="gap: 14px">
        <div class="row" style="gap: 14px">
          <img :src="iconOf(active)" class="pic" alt="" @error="(e) => (e.target.style.visibility = 'hidden')" />
          <div class="grow" style="min-width: 0">
            <b>{{ active.name }}</b>
            <div class="small muted ellipsis">{{ active.description }}</div>
            <button class="url mono" :title="$t('Kopiuj adres')" @click="copy(active.url)"><Link2 /> <span class="ellipsis">{{ active.url }}</span> <Copy /></button>
          </div>
        </div>
        <div class="grid grid-2">
          <Toggle v-model="opts.require" :label="$t('Wymagaj paczki')" :hint="$t('Gracze, którzy odmówią pobrania, zostaną rozłączeni')" />
          <div class="field">
            <label>{{ $t('Komunikat przy pobieraniu') }}</label>
            <input v-model="opts.prompt" class="input" maxlength="300" :placeholder="$t('np. Ten serwer używa własnych tekstur')" />
          </div>
        </div>
        <div class="row"><span class="spacer" /><button class="btn primary" @click="activate(active)"><Save /> {{ $t('Zapisz opcje') }}</button></div>
      </div>
      <div v-else class="card-body small muted">
        <template v-if="d.external_url">{{ $t('Serwer używa zewnętrznej paczki:') }} <span class="mono">{{ d.external_url }}</span> {{ $t('(ustawionej ręcznie w server.properties).') }}</template>
        <template v-else>{{ $t('Brak aktywnej paczki. Wgraj własną lub pobierz z Modrinth/CurseForge, a potem kliknij „Ustaw jako paczkę serwera”.') }}</template>
      </div>
    </section>

    <!-- library -->
    <section class="card">
      <div class="card-head"><Library class="hi" /><h3>{{ $t('Paczki na serwerze') }}</h3><span class="badge">{{ d.packs.length }}</span>
        <label class="btn sm"><Upload /> {{ $t('Wgraj ZIP') }}<input type="file" accept=".zip" hidden @change="upload($event.target.files[0]); $event.target.value = ''" /></label></div>
      <div class="drop" :class="{ over: dragOver }" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="dragOver = false; upload($event.dataTransfer.files[0])">
        <div v-if="uploading" class="row small" style="width: 100%"><Loader2 class="spin" style="width: 16px" /> {{ $t('Wysyłanie…') }} <div class="progress grow"><div :style="{ width: uploading * 100 + '%' }" /></div></div>
        <div v-else-if="!d.packs.length" class="muted small"><Upload class="ui" /> {{ $t('Przeciągnij tutaj plik .zip z paczką zasobów') }}</div>
        <div v-else class="packs">
          <div v-for="p in d.packs" :key="p.id" class="pack" :class="{ act: p.id === d.active }">
            <img :src="iconOf(p)" class="pic" alt="" @error="(e) => (e.target.style.visibility = 'hidden')" />
            <div class="grow" style="min-width: 0">
              <div class="row" style="gap: 6px"><b class="ellipsis">{{ p.name }}</b><span v-if="p.id === d.active" class="badge green">{{ $t('aktywna') }}</span></div>
              <div class="tiny muted ellipsis">{{ p.description || '—' }}</div>
              <div class="row wrap tiny" style="gap: 6px; margin-top: 4px">
                <span class="badge">{{ $t(SOURCE[p.source]) }}</span>
                <span v-if="p.pack_format" class="badge" :title="'pack_format w pack.mcmeta'">{{ $t('format {n}', { n: p.pack_format }) }}</span>
                <span class="badge">{{ formatBytes(p.size) }}</span>
                <span v-if="p.fixed" class="badge blue" :title="$t('Paczka była zapakowana w folder – panel poprawił strukturę')">{{ $t('poprawiono strukturę') }}</span>
              </div>
            </div>
            <div class="row" style="gap: 4px">
              <button v-if="p.id !== d.active" class="btn sm primary" :disabled="!d.base_url" @click="activate(p)"><Check /> {{ $t('Ustaw jako paczkę serwera') }}</button>
              <button class="btn sm ghost icon" :title="$t('Pobierz')" @click="download(`/servers/${id}/resourcepacks/${p.id}/download`)"><Download /></button>
              <button class="btn sm ghost icon" :title="$t('Usuń')" @click="remove(p)"><Trash2 /></button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- browse -->
    <section class="card">
      <div class="card-head"><Search class="hi" /><h3>{{ $t('Pobierz z Modrinth / CurseForge') }}</h3></div>
      <div class="card-body col" style="gap: 12px">
        <div class="row wrap" style="gap: 10px">
          <div class="seg">
            <button :class="{ active: source === 'modrinth' }" @click="source = 'modrinth'; search(true)">Modrinth</button>
            <button :class="{ active: source === 'curseforge' }" @click="source = 'curseforge'; search(true)">CurseForge</button>
          </div>
          <div class="searchbox grow"><Search /><input v-model="q" :placeholder="$t('Szukaj paczek, np. Faithful, Fresh Animations…')" @keydown.enter="search(true)" /></div>
          <Toggle v-model="anyVersion" :label="$t('Wszystkie wersje')" @update:model-value="search(true)" />
        </div>
        <div v-if="source === 'curseforge' && !cfOk" class="callout warn small"><KeyRound />{{ $t('CurseForge wymaga klucza API – dodaj go w') }} <router-link to="/settings">{{ $t('Ustawieniach') }}</router-link>.</div>
        <div v-else-if="error" class="callout warn small"><AlertTriangle />{{ $tm(error) }}</div>
        <div v-if="searching && !results.length" class="empty"><Loader2 class="spin" /></div>
        <div class="results">
          <div v-for="r in results" :key="r.id" class="res">
            <img v-if="r.icon" :src="r.icon" class="pic" alt="" referrerpolicy="no-referrer" loading="lazy" />
            <div v-else class="pic" />
            <div class="grow" style="min-width: 0">
              <a :href="r.url" target="_blank" rel="noopener" class="title ellipsis">{{ r.title }}</a>
              <div class="tiny muted desc">{{ r.description }}</div>
              <span class="badge" style="margin-top: 6px"><Download class="bi" /> {{ compact(r.downloads) }}</span>
            </div>
            <button class="btn sm" :disabled="busy[r.id]" @click="install(r)"><Loader2 v-if="busy[r.id]" class="spin" /><Download v-else /> {{ $t('Pobierz na serwer') }}</button>
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
import { AlertTriangle, Info, RotateCcw, Sparkles, PowerOff, Link2, Copy, Save, Library, Upload, Loader2, Check, Download, Trash2, Search, KeyRound } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import { api, download, uploadWithProgress } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { compact, copyText, formatBytes } from '../../utils'

const live = inject('live')
const id = live.summary.id
const SOURCE = { upload: 'wgrana', modrinth: 'Modrinth', curseforge: 'CurseForge' }
const d = ref(null)
const opts = reactive({ require: false, prompt: '' })
const restartNeeded = ref(false)
const dragOver = ref(false)
const uploading = ref(null)
const busy = reactive({})
const source = ref('modrinth')
const q = ref('')
const anyVersion = ref(false)
const results = ref([])
const total = ref(0)
const searching = ref(false)
const error = ref('')
const cfOk = ref(true)
const active = computed(() => d.value?.packs.find((p) => p.id === d.value.active))
const iconOf = (p) => (p.has_icon ? `/api/servers/${id}/resourcepacks/${p.id}/icon` : p.icon_url || '')

async function load() {
  try {
    d.value = await api.get(`/servers/${id}/resourcepacks`)
    opts.require = d.value.require
    opts.prompt = d.value.prompt
  } catch (e) { toastError(e) }
}
onMounted(async () => {
  await load()
  search(true)
  try { cfOk.value = (await api.get('/settings')).curseforge_configured } catch {}
})

async function upload(file) {
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  fd.append('name', file.name.replace(/\.zip$/i, ''))
  uploading.value = 0.01
  try {
    const p = await uploadWithProgress(`/servers/${id}/resourcepacks/upload`, fd, (v) => (uploading.value = v))
    toast(`Dodano paczkę „${p.name}”${p.fixed ? ' (poprawiono strukturę ZIP)' : ''}`, 'success')
    load()
  } catch (e) { toastError(e) } finally { uploading.value = null }
}
async function activate(p) {
  try {
    const r = await api.post(`/servers/${id}/resourcepacks/${p.id}/activate`, { require: opts.require, prompt: opts.prompt })
    if (r.restart_required) restartNeeded.value = true
    toast(`„${p.name}” jest teraz paczką serwera`, 'success')
    load()
  } catch (e) { toastError(e) }
}
async function deactivate() {
  try { const r = await api.post(`/servers/${id}/resourcepacks/deactivate`); if (r.restart_required) restartNeeded.value = true; load() } catch (e) { toastError(e) }
}
async function remove(p) {
  if (!(await confirmDialog({ title: `Usunąć „${p.name}”?`, confirmText: 'Usuń', danger: true }))) return
  try { await api.del(`/servers/${id}/resourcepacks/${p.id}`); load() } catch (e) { toastError(e) }
}
async function search(reset) {
  if (source.value === 'curseforge' && !cfOk.value) { results.value = []; return }
  searching.value = true
  error.value = ''
  try {
    const off = reset ? 0 : results.value.length
    const r = await api.get(`/servers/${id}/resourcepacks/search?source=${source.value}&q=${encodeURIComponent(q.value)}&offset=${off}&any_version=${anyVersion.value}`)
    results.value = reset ? r.items : [...results.value, ...r.items]
    total.value = r.total
  } catch (e) { error.value = e.message } finally { searching.value = false }
}
async function install(r) {
  busy[r.id] = true
  try { const p = await api.post(`/servers/${id}/resourcepacks/install`, { source: r.source, project_id: r.id }); toast(`Pobrano „${p.name}” na serwer`, 'success'); load() } catch (e) { toastError(e) } finally { busy[r.id] = false }
}
async function restart() {
  try { await api.post(`/servers/${id}/power`, { action: 'restart' }); restartNeeded.value = false } catch (e) { toastError(e) }
}
async function copy(t) { if (await copyText(t)) toast('Skopiowano adres', 'success', 1500) }
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.pic { width: 48px; height: 48px; border-radius: 8px; image-rendering: pixelated; background: var(--surface-3); flex: none; object-fit: cover; }
.url { display: flex; align-items: center; gap: 6px; max-width: 100%; margin-top: 6px; border: 1px solid var(--border); background: var(--surface-2); color: var(--text-2); border-radius: 6px; padding: 3px 8px; font-size: 11.5px; cursor: pointer; }
.url svg { width: 13px; height: 13px; flex: none; }
.drop { padding: 8px; min-height: 80px; display: flex; align-items: center; justify-content: center; }
.drop.over { background: var(--accent-soft); outline: 1.5px dashed var(--accent); outline-offset: -6px; }
.ui { width: 16px; height: 16px; vertical-align: -3px; }
.packs { display: flex; flex-direction: column; width: 100%; }
.pack { display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: var(--radius-sm); }
.pack:hover { background: var(--surface-2); }
.pack.act { background: var(--accent-soft); }
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface-2); border: 1px solid var(--border); }
.seg button { height: 28px; padding: 0 12px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-weight: 500; cursor: pointer; }
.seg button.active { background: var(--surface-3); color: var(--text); }
.searchbox { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 12px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); min-width: 200px; }
.searchbox svg { width: 16px; height: 16px; color: var(--muted); }
.searchbox input { flex: 1; background: none; border: 0; outline: none; color: var(--text); font: inherit; }
.results { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 8px; }
.res { display: flex; align-items: center; gap: 12px; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); }
.title { color: var(--text); font-weight: 600; display: block; }
.desc { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.bi { width: 12px; height: 12px; }
</style>
