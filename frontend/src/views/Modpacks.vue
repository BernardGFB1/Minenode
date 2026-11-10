<template>
  <div class="page">
    <div class="page-head">
      <div class="grow">
        <h1>{{ $t('Modpacki') }}</h1>
        <div class="sub">{{ $t('Zainstaluj gotową paczkę modów jako nowy serwer – loader, mody i konfiguracja zostaną pobrane automatycznie.') }}</div>
      </div>
    </div>

    <div class="row wrap" style="margin-bottom: 16px; gap: 12px">
      <div class="seg">
        <button :class="{ active: source === 'modrinth' }" @click="setSource('modrinth')">Modrinth</button>
        <button :class="{ active: source === 'curseforge' }" @click="setSource('curseforge')">CurseForge</button>
        <button :class="{ active: source === 'file' }" @click="setSource('file')"><Upload /> {{ $t('Z pliku') }}</button>
      </div>
      <template v-if="source !== 'file'">
        <div class="searchbox grow">
          <Search />
          <input v-model="q" :placeholder="$t('Szukaj modpacków, np. All the Mods, Cobblemon, Create…')" @keydown.enter="search(true)" />
        </div>
        <input v-model="mcFilter" class="input" style="width: 130px" :placeholder="$t('Wersja MC')" @keydown.enter="search(true)" />
        <button class="btn primary" @click="search(true)">{{ $t('Szukaj') }}</button>
      </template>
    </div>

    <div v-if="source === 'curseforge' && settings && !settings.curseforge_configured" class="callout warn" style="margin-bottom: 16px">
      <KeyRound />
      <div>{{ $t('CurseForge wymaga klucza API. Wygeneruj go na') }} <a href="https://console.curseforge.com/" target="_blank" rel="noopener">{{ $t('console.curseforge.com') }}</a>
        {{ $t('i wklej w') }} <router-link to="/settings">{{ $t('Ustawieniach panelu') }}</router-link>.</div>
    </div>

    <!-- upload -->
    <div v-if="source === 'file'" class="card card-body col" style="gap: 16px; max-width: 760px">
      <label class="drop" :class="{ over: dragOver, has: file }" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
        <input type="file" accept=".mrpack,.zip" hidden @change="file = $event.target.files[0]; prefill()" />
        <FileArchive />
        <div v-if="file"><b>{{ file.name }}</b> <span class="muted">({{ formatBytes(file.size) }})</span></div>
        <div v-else><b>{{ $t('Upuść plik .mrpack lub ZIP z CurseForge') }}</b><div class="small muted">{{ $t('albo kliknij, aby wybrać') }}</div></div>
      </label>
      <InstallForm v-model="installForm" :settings="settings" :sys="sys" />
      <div v-if="uploadProgress != null" class="progress"><div :style="{ width: uploadProgress * 100 + '%' }" /></div>
      <div class="row"><span class="spacer" />
        <button class="btn primary" :disabled="!file || !installForm.name || !installForm.eula || busy" @click="installFile">
          <Loader2 v-if="busy" class="spin" /><Rocket v-else /> {{ $t('Zainstaluj z pliku') }}
        </button>
      </div>
    </div>

    <!-- results -->
    <template v-else>
      <div v-if="loading && !items.length" class="empty"><Loader2 class="spin" /></div>
      <div v-else-if="error" class="callout warn"><AlertTriangle />{{ $tm(error) }}</div>
      <div class="packs">
        <article v-for="p in items" :key="p.id" class="card pack" @click="open(p)">
          <img v-if="p.icon" :src="p.icon" class="picon" alt="" loading="lazy" referrerpolicy="no-referrer" />
          <div v-else class="picon ph"><Package /></div>
          <div class="grow" style="min-width: 0">
            <h3 class="ellipsis">{{ p.title }}</h3>
            <div class="tiny muted ellipsis">{{ p.author ? $t('od {who}', { who: p.author }) : '' }}</div>
            <p class="small text-2 desc">{{ p.description }}</p>
            <div class="row wrap" style="gap: 6px; margin-top: 8px">
              <span class="badge"><Download style="width: 12px; height: 12px" /> {{ compact(p.downloads) }}</span>
              <span v-for="l in p.loaders" :key="l" class="badge amber">{{ l }}</span>
              <span v-for="v in p.versions" :key="v" class="badge mono">{{ v }}</span>
            </div>
          </div>
        </article>
      </div>
      <div v-if="items.length && items.length < total" class="row" style="justify-content: center; margin-top: 18px">
        <button class="btn" :disabled="loading" @click="search(false)"><Loader2 v-if="loading" class="spin" /> {{ $t('Pokaż więcej') }}</button>
      </div>
      <div v-if="!loading && !items.length && !error" class="empty">{{ $t('Brak wyników.') }}</div>
    </template>

    <Modal :open="!!selected" :title="selected?.title" width="760px" @close="selected = null">
      <div v-if="selected" class="col" style="gap: 18px">
        <div class="row" style="gap: 14px; align-items: flex-start">
          <img v-if="selected.icon" :src="selected.icon" class="picon" alt="" referrerpolicy="no-referrer" />
          <div class="grow">
            <p class="text-2">{{ selected.description }}</p>
            <a v-if="selected.url" :href="selected.url" target="_blank" rel="noopener" class="small">{{ $t('Zobacz na {site}', { site: selected.source === 'modrinth' ? 'Modrinth' : 'CurseForge' }) }} ↗</a>
          </div>
        </div>
        <div class="field">
          <label>{{ $t('Wersja paczki') }}</label>
          <div v-if="versionsLoading" class="muted small row"><Loader2 class="spin" style="width: 14px" /> {{ $t('Ładowanie wersji…') }}</div>
          <div v-else class="vlist">
            <label v-for="v in versions" :key="v.id" class="vitem" :class="{ active: versionId === v.id }">
              <input v-model="versionId" type="radio" :value="v.id" />
              <div class="grow" style="min-width: 0">
                <div class="ellipsis" style="font-weight: 550">{{ v.name }}</div>
                <div class="tiny muted">{{ formatDate(v.date) }} · {{ formatBytes(v.size) }}<template v-if="v.server_pack"> {{ $t('· ma paczkę serwerową') }}</template></div>
              </div>
              <span v-if="v.type !== 'release'" class="badge amber">{{ v.type }}</span>
              <span v-for="l in v.loaders.slice(0, 2)" :key="l" class="badge">{{ l }}</span>
              <span class="badge mono">{{ v.game_versions.slice(-1)[0] }}</span>
            </label>
          </div>
        </div>
        <template v-if="selected.source === 'modrinth' && versionId">
          <div v-if="sp.loading" class="callout small"><Loader2 class="spin" /><div>{{ $t('Szukam oficjalnej paczki serwerowej…') }}</div></div>
          <div v-else-if="sp.data?.available" class="callout green small"><ServerCog /><div>{{ $t('Autor udostępnia oficjalną paczkę serwerową ({file}) – panel zainstaluje ją zamiast paczki dla graczy.', { file: sp.data.match.file }) }}</div></div>
          <div v-else-if="sp.data?.needs_key" class="callout info small"><KeyRound /><div>{{ $t('Modrinth nie ma osobnych paczek serwerowych. Dodaj darmowy klucz CurseForge w') }} <router-link to="/settings">{{ $t('Ustawieniach') }}</router-link>{{ $t(', a panel sam użyje oficjalnej paczki serwerowej, jeśli autor ją udostępnia. Bez klucza panel wyłączy mody klienckie automatycznie.') }}</div></div>
          <div v-else-if="sp.data" class="callout small"><Info /><div>{{ $t('Brak oficjalnej paczki serwerowej dla tej wersji – panel użyje paczki z Modrinth i automatycznie wyłączy mody działające tylko u graczy.') }}</div></div>
        </template>
        <div v-else-if="selected.source === 'curseforge' && versionId" class="callout small" :class="versions.find((v) => v.id === versionId)?.server_pack ? 'green' : ''">
          <ServerCog v-if="versions.find((v) => v.id === versionId)?.server_pack" /><Info v-else />
          <div>{{ versions.find((v) => v.id === versionId)?.server_pack ? $t('Ta wersja ma oficjalną paczkę serwerową – panel zainstaluje ją zamiast paczki dla graczy.') : $t('Ta wersja nie ma paczki serwerowej – panel pobierze mody z listy i wyłączy te działające tylko u graczy.') }}</div>
        </div>
        <InstallForm v-model="installForm" :settings="settings" :sys="sys" />
      </div>
      <template #footer>
        <button class="btn" @click="selected = null">{{ $t('Anuluj') }}</button>
        <button class="btn primary" :disabled="!versionId || !installForm.name || !installForm.eula || busy" @click="installSelected">
          <Loader2 v-if="busy" class="spin" /><Rocket v-else /> {{ $t('Zainstaluj modpack') }}
        </button>
      </template>
    </Modal>
  </div>
</template>
<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Upload, Package, Download, Loader2, Rocket, AlertTriangle, KeyRound, FileArchive, ServerCog, Info } from 'lucide-vue-next'
import Modal from '../components/Modal.vue'
import InstallForm from '../components/InstallForm.vue'
import { api, uploadWithProgress } from '../api'
import { toast, toastError } from '../store'
import { t } from '../i18n'
import { refreshFleet } from '../servers'
import { compact, formatBytes, formatDate } from '../utils'

const router = useRouter()
const source = ref('modrinth')
const q = ref('')
const mcFilter = ref('')
const items = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const settings = ref(null)
const sys = ref(null)
const selected = ref(null)
const versions = ref([])
const versionsLoading = ref(false)
const versionId = ref(null)
const sp = reactive({ loading: false, data: null })
let spReq = 0
watch(versionId, async (vid) => {
  sp.data = null
  if (!vid || selected.value?.source !== 'modrinth') return
  const req = ++spReq
  sp.loading = true
  try {
    const r = await api.get(`/modpacks/server-pack?version_id=${encodeURIComponent(vid)}`)
    if (req === spReq) sp.data = r
  } catch { if (req === spReq) sp.data = { available: false } } finally { if (req === spReq) sp.loading = false }
})
const busy = ref(false)
const file = ref(null)
const dragOver = ref(false)
const uploadProgress = ref(null)
const installForm = ref({ name: '', subdomain: '', eula: false, max_ram_mb: 6144, min_ram_mb: 2048 })

onMounted(async () => {
  search(true)
  try { [settings.value, sys.value] = await Promise.all([api.get('/settings'), api.get('/system')]) } catch {}
})

function setSource(s) {
  source.value = s
  items.value = []
  if (s !== 'file') search(true)
}

async function search(reset) {
  if (source.value === 'file') return
  if (source.value === 'curseforge' && settings.value && !settings.value.curseforge_configured) { items.value = []; return }
  loading.value = true
  error.value = ''
  try {
    const offset = reset ? 0 : items.value.length
    const r = await api.get(`/modpacks/search?source=${source.value}&q=${encodeURIComponent(q.value)}&offset=${offset}&mc=${encodeURIComponent(mcFilter.value)}`)
    items.value = reset ? r.items : [...items.value, ...r.items]
    total.value = r.total
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function open(p) {
  selected.value = p
  versionId.value = null
  versions.value = []
  installForm.value = { ...installForm.value, name: p.title.slice(0, 64), subdomain: '' }
  versionsLoading.value = true
  try {
    versions.value = await api.get(`/modpacks/${p.source}/${p.id}/versions`)
    versionId.value = (versions.value.find((v) => v.type === 'release') || versions.value[0])?.id || null
  } catch (e) { toastError(e) } finally { versionsLoading.value = false }
}

const javaOf = (f) => ({ min_ram_mb: Math.min(f.min_ram_mb, f.max_ram_mb), max_ram_mb: f.max_ram_mb, flags_preset: 'aikar' })

async function installSelected() {
  busy.value = true
  try {
    const f = installForm.value
    const s = await api.post('/servers/modpack', {
      source: selected.value.source, project_id: selected.value.id, version_id: versionId.value,
      name: f.name, subdomain: f.subdomain || null, eula: f.eula, java: javaOf(f), icon_url: selected.value.icon,
    })
    toast(t('Instalowanie modpacka „{name}”…', { name: s.name }), 'success')
    refreshFleet()
    router.push(`/servers/${s.id}/console`)
  } catch (e) { toastError(e) } finally { busy.value = false }
}

function onDrop(e) {
  dragOver.value = false
  file.value = e.dataTransfer.files[0]
  prefill()
}
function prefill() {
  if (file.value && !installForm.value.name) installForm.value.name = file.value.name.replace(/\.(mrpack|zip)$/i, '').slice(0, 64)
}

async function installFile() {
  busy.value = true
  uploadProgress.value = 0
  try {
    const f = installForm.value
    const fd = new FormData()
    fd.append('file', file.value)
    fd.append('name', f.name)
    fd.append('subdomain', f.subdomain || '')
    fd.append('max_ram_mb', f.max_ram_mb)
    fd.append('eula', f.eula ? 'true' : 'false')
    const s = await uploadWithProgress('/servers/modpack/upload', fd, (p) => (uploadProgress.value = p))
    toast(t('Instalowanie modpacka „{name}”…', { name: s.name }), 'success')
    refreshFleet()
    router.push(`/servers/${s.id}/console`)
  } catch (e) { toastError(e) } finally { busy.value = false; uploadProgress.value = null }
}
</script>
<style scoped>
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface); border: 1px solid var(--border); }
.seg button { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 14px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-weight: 500; cursor: pointer; }
.seg button svg { width: 15px; height: 15px; }
.seg button.active { background: var(--surface-3); color: var(--text); }
.searchbox { display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); min-width: 240px; }
.searchbox:focus-within { border-color: var(--accent); box-shadow: var(--ring); }
.searchbox svg { width: 16px; height: 16px; color: var(--muted); }
.searchbox input { flex: 1; background: none; border: 0; outline: none; color: var(--text); font: inherit; }
.packs { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 14px; }
.pack { display: flex; gap: 14px; padding: 16px; cursor: pointer; transition: border-color 0.15s; }
.pack:hover { border-color: var(--border-strong); }
.picon { width: 64px; height: 64px; border-radius: 12px; object-fit: cover; flex: none; background: var(--surface-3); }
.picon.ph { display: grid; place-items: center; color: var(--muted); }
.desc { margin-top: 6px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.vlist { display: flex; flex-direction: column; gap: 4px; max-height: 260px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius); padding: 4px; }
.vitem { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; }
.vitem:hover { background: var(--surface-2); }
.vitem.active { background: var(--accent-soft); }
.drop { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 34px; border: 1.5px dashed var(--border-strong); border-radius: var(--radius-lg); text-align: center; cursor: pointer; background: var(--surface-2); }
.drop svg { width: 28px; height: 28px; color: var(--muted); }
.drop.over, .drop.has { border-color: var(--accent); background: var(--accent-soft); }
.drop.has svg { color: var(--accent); }
</style>
