<template>
  <div class="col" style="gap: 16px">
    <div class="field">
      <label>{{ $t('Typ świata') }}</label>
      <div class="row wrap" style="gap: 6px">
        <button v-for="p in PRESETS" :key="p.id" type="button" class="chip" :class="{ active: model.preset === p.id }" @click="model.preset = p.id">{{ $t(p.label) }}</button>
      </div>
      <input v-if="model.preset === 'custom'" v-model.trim="model.custom" class="input mono" maxlength="100" :placeholder="$t('np. terralith:default')" />
      <span v-if="model.preset === 'custom'" class="hint">{{ $t('Identyfikator „world preset” z datapacka lub moda (level-type w server.properties).') }}</span>
    </div>

    <div class="field">
      <label>{{ $t('Ziarno (seed)') }}</label>
      <input v-model="model.seed" class="input mono" maxlength="100" :placeholder="$t('losowe')" />
    </div>

    <div class="field">
      <label>{{ $t('Generator świata') }}</label>
      <div class="files">
        <div v-for="f in model.files" :key="f.token || f.uid" class="frow" :class="{ bad: f.error || problem(f) }">
          <Loader2 v-if="f.uploading" class="spin fi" />
          <component :is="KIND[f.kind]?.icon || FileWarning" v-else class="fi" />
          <div class="grow" style="min-width: 0">
            <div class="ellipsis"><b>{{ f.name || f.filename }}</b> <span v-if="f.kind" class="badge" :class="KIND[f.kind].badge">{{ $t(KIND[f.kind].label) }}</span></div>
            <div class="tiny muted ellipsis">{{ f.error ? $tm(f.error) : problem(f) || (f.uploading ? $t(f.fromModrinth ? 'Pobieranie z Modrinth…' : 'Wysyłanie…') : rowInfo(f)) }}</div>
          </div>
          <button type="button" class="btn sm ghost icon" :title="$t('Usuń')" @click="remove(f)"><X /></button>
        </div>
        <div class="row wrap" style="gap: 6px">
          <button type="button" class="btn sm" :class="{ active: browse }" @click="browse = !browse"><Search /> {{ $t('Szukaj na Modrinth') }}</button>
          <button type="button" class="btn sm ghost" @click="input?.click()"><Upload /> {{ $t(addLabel) }}</button>
        </div>
        <input ref="input" type="file" multiple :accept="accept" hidden @change="pick" />
      </div>
      <span class="hint">{{ $t(hint) }}</span>

      <div v-if="browse" class="browse">
        <input v-model="q" class="input" :placeholder="$t('Szukaj generatorów świata, np. Terralith, Tectonic')" maxlength="100" />
        <div v-if="!mc" class="tiny muted">{{ $t('Najpierw wybierz wersję Minecrafta.') }}</div>
        <div v-else class="mlist">
          <div v-for="p in results" :key="p.id" class="mrow">
            <img v-if="p.icon" :src="p.icon" alt="" referrerpolicy="no-referrer" />
            <div v-else class="noicon"><Globe2 /></div>
            <div class="grow" style="min-width: 0">
              <div class="ellipsis"><b>{{ p.title }}</b> <span class="tiny muted">{{ p.author }} · {{ compact(p.downloads) }} ↓</span></div>
              <div class="tiny muted ellipsis">{{ p.description }}</div>
            </div>
            <a :href="p.url" target="_blank" rel="noopener" class="btn sm ghost icon" :title="$t('Otwórz na Modrinth')"><ExternalLink /></a>
            <button type="button" class="btn sm" :disabled="isAdded(p.id) || adding[p.id]" @click="addFromModrinth(p)">
              <Loader2 v-if="adding[p.id]" class="spin" /><Check v-else-if="isAdded(p.id)" /><Plus v-else />
              {{ isAdded(p.id) ? $t('Dodano') : $t('Dodaj') }}
            </button>
          </div>
          <div v-if="searching" class="tiny muted"><Loader2 class="spin" style="width: 12px" /> {{ $t('Szukanie…') }}</div>
          <div v-else-if="!results.length" class="tiny muted">{{ $t('Brak generatorów świata dla tego silnika i wersji.') }}</div>
          <button v-if="!searching && results.length < total" type="button" class="btn sm ghost" @click="search(true)">{{ $t('Pokaż więcej') }}</button>
        </div>
      </div>
    </div>

    <div v-if="hasPlugin && pluginEngine" class="field">
      <label>{{ $t('Generator w bukkit.yml') }}</label>
      <input v-model.trim="model.generator" class="input mono" maxlength="100" :placeholder="$t('np. Terra:OVERWORLD')" />
      <span class="hint">{{ $t('Nazwa generatora z pluginu – zwykle nazwa pluginu, czasem z dopiskiem (sprawdź w jego dokumentacji). Puste pole: plugin zostanie wgrany bez zmiany generatora.') }}</span>
    </div>

    <div v-if="active" class="callout info small"><Globe2 />{{ $t('Wszystko trafi na serwer przed pierwszym startem, więc świat od razu powstanie z tym generatorem. Późniejsza zmiana wymaga wygenerowania nowego świata.') }}</div>
  </div>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { Plus, X, Loader2, Package, Puzzle, Plug, FileWarning, Globe2, Search, Upload, ExternalLink, Check } from 'lucide-vue-next'
import { api } from '../api'
import { compact, formatBytes } from '../utils'
import { toast } from '../store'
import { t } from '../i18n'

const props = defineProps({ engine: String, mc: String })
const model = defineModel({ type: Object, required: true })
const input = ref(null)

const PRESETS = [
  { id: '', label: 'Domyślny' }, { id: 'flat', label: 'Płaski' }, { id: 'large_biomes', label: 'Duże biomy' },
  { id: 'amplified', label: 'Wzmocniony' }, { id: 'custom', label: 'Własny…' },
]
const KIND = {
  datapack: { label: 'Datapack', badge: 'green', icon: Package },
  mod: { label: 'Mod', badge: 'amber', icon: Puzzle },
  plugin: { label: 'Plugin', badge: 'blue', icon: Plug },
}
// Mirrors app/worldgen.py: what each engine can load.
const ENGINE_KINDS = {
  vanilla: ['datapack'], paper: ['datapack', 'plugin'], purpur: ['datapack', 'plugin'],
  fabric: ['datapack', 'mod'], quilt: ['datapack', 'mod'], forge: ['datapack', 'mod'], neoforge: ['datapack', 'mod'],
  custom: ['datapack', 'plugin', 'mod'],
}
const ENGINE_LOADERS = {
  fabric: ['fabric'], quilt: ['quilt', 'fabric'], forge: ['forge'], neoforge: ['neoforge', 'forge'],
  custom: ['fabric', 'quilt', 'forge', 'neoforge'],
}
const kinds = computed(() => ENGINE_KINDS[props.engine] || ['datapack'])
const pluginEngine = computed(() => kinds.value.includes('plugin'))
const hasPlugin = computed(() => model.value.files.some((f) => f.kind === 'plugin'))
const addLabel = computed(() => ({
  datapack: 'Dodaj datapack (.zip)', 'datapack,plugin': 'Dodaj datapack lub plugin', 'datapack,mod': 'Dodaj datapack lub mod',
}[kinds.value.join(',')] || 'Dodaj datapack, mod lub plugin'))
const hint = computed(() => ({
  datapack: 'Vanilla wczyta tylko datapacki z generatorem świata (np. Terralith, Tectonic).',
  'datapack,plugin': 'Datapacki (.zip) i pluginy generatora (.jar), np. Terra lub Iris.',
  'datapack,mod': 'Datapacki (.zip) i mody generatora (.jar) dla tego loadera.',
}[kinds.value.join(',')] || 'Datapacki (.zip) oraz mody lub pluginy (.jar) – panel rozpozna je sam.'))
const accept = computed(() => (kinds.value.length === 1 ? '.zip' : '.zip,.jar'))
const active = computed(() => model.value.preset || model.value.seed || model.value.files.length)

function problem(f) {
  if (!f.kind) return null
  if (!kinds.value.includes(f.kind)) return t('Ten silnik nie wczyta tego pliku – zmień silnik albo usuń plik.')
  if (f.kind === 'mod' && !(f.loaders || []).some((l) => (ENGINE_LOADERS[props.engine] || []).includes(l))) {
    return t('Mod dla {l} – nie zadziała na tym silniku.', { l: (f.loaders || []).join('/') })
  }
  return null
}

function rowInfo(f) {
  const where = f.source === 'modrinth' ? `Modrinth ${f.version_number || ''}`.trim() : f.filename
  const req = f.required_by ? ` · ${t('wymagany przez {name}', { name: f.required_by })}` : ''
  return `${where} · ${formatBytes(f.size || 0)}${req}`
}

// ---------------------------------------------------------------- Modrinth search
const browse = ref(false)
const q = ref('')
const results = ref([])
const total = ref(0)
const searching = ref(false)
const adding = ref({})
let searchTimer
let searchSeq = 0
const isAdded = (id) => model.value.files.some((f) => f.project_id === id && !f.error)

async function search(more = false) {
  if (!props.mc) return
  const seq = ++searchSeq
  searching.value = true
  try {
    const params = new URLSearchParams({ engine: props.engine, mc: props.mc, q: q.value, offset: more ? results.value.length : 0 })
    const r = await api.get(`/worldgen/modrinth?${params}`)
    if (seq !== searchSeq) return
    results.value = more ? [...results.value, ...r.items] : r.items
    total.value = r.total
  } catch (e) {
    if (seq === searchSeq) { results.value = []; total.value = 0; toast(e.message, 'error') }
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}
watch([browse, q, () => props.engine, () => props.mc], ([open]) => {
  clearTimeout(searchTimer)
  if (open) searchTimer = setTimeout(() => search(), q.value ? 350 : 0)
})

async function addFromModrinth(p) {
  adding.value = { ...adding.value, [p.id]: true }
  // Rows are compared by uid: the array holds reactive proxies, never the plain object pushed here.
  const id = ++uid
  model.value.files.push({ uid: id, name: p.title, filename: p.title, uploading: true, fromModrinth: true, project_id: p.id })
  const row = () => model.value.files.find((f) => f.uid === id)
  try {
    const r = await api.post('/worldgen/modrinth', { project_id: p.id, engine: props.engine, mc_version: props.mc })
    const known = new Set(model.value.files.filter((f) => f.uid !== id && f.project_id && !f.error).map((f) => f.project_id))
    const fresh = r.items.filter((i) => !known.has(i.project_id))
    const at = model.value.files.findIndex((f) => f.uid === id)
    if (at >= 0) model.value.files.splice(at, 1, ...fresh)
    for (const w of r.warnings || []) toast(w, 'info', 6000)
    const plugin = fresh.find((i) => i.kind === 'plugin')
    if (plugin && !model.value.generator && plugin.name) model.value.generator = plugin.name
  } catch (e) {
    if (row()) Object.assign(row(), { uploading: false, error: e.message })
  } finally {
    adding.value = { ...adding.value, [p.id]: false }
  }
}

let uid = 0
async function pick(ev) {
  const picked = [...(ev.target.files || [])]
  ev.target.value = ''
  for (const file of picked) {
    const id = ++uid
    model.value.files.push({ uid: id, filename: file.name, uploading: true })
    const row = () => model.value.files.find((f) => f.uid === id) // removed meanwhile -> undefined
    const fd = new FormData()
    fd.append('file', file, file.name)
    try {
      const info = await api.upload('/worldgen/stage', fd)
      if (row()) Object.assign(row(), info, { uploading: false })
      if (info.kind === 'plugin' && !model.value.generator && info.name) model.value.generator = info.name
    } catch (e) {
      if (row()) Object.assign(row(), { uploading: false, error: e.message })
    }
  }
}
function remove(f) {
  model.value.files = model.value.files.filter((x) => x !== f)
  if (!hasPlugin.value) model.value.generator = ''
}
watch(hasPlugin, (v) => { if (!v) model.value.generator = '' })

/** What the create request needs, or null when the default world is wanted. */
function spec() {
  const files = model.value.files.filter((f) => f.token && !f.error)
  const level = model.value.preset === 'custom' ? model.value.custom : model.value.preset
  if (!level && !model.value.seed && !files.length) return null
  return {
    level_type: level || '', seed: model.value.seed || '', files: files.map((f) => f.token),
    generator: hasPlugin.value && pluginEngine.value ? model.value.generator || '' : '',
  }
}
const blocking = computed(() => model.value.files.some((f) => f.uploading || f.error || problem(f))
  || (model.value.preset === 'custom' && !model.value.custom))
defineExpose({ spec, blocking })
</script>
<style scoped>
.chip { height: 30px; padding: 0 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface-2); color: var(--text-2); font: inherit; font-size: 13px; cursor: pointer; }
.chip:hover { border-color: var(--border-strong); }
.chip.active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.files { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.frow { display: flex; gap: 10px; align-items: center; width: 100%; padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--surface-2); }
.frow.bad { border-color: color-mix(in srgb, var(--danger) 55%, var(--border)); }
.frow.bad .tiny { color: var(--danger); }
.fi { width: 18px; height: 18px; color: var(--muted); flex: none; }
.btn.icon { padding: 0 6px; }
.btn.active { border-color: var(--accent); color: var(--accent); }
.browse { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; padding: 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface); }
.mlist { display: flex; flex-direction: column; gap: 4px; max-height: 360px; overflow-y: auto; }
.mrow { display: flex; gap: 10px; align-items: center; padding: 6px 4px; border-radius: var(--radius-sm); }
.mrow:hover { background: var(--surface-2); }
.mrow img, .noicon { width: 36px; height: 36px; border-radius: 8px; flex: none; object-fit: cover; background: var(--surface-3); display: grid; place-items: center; color: var(--muted); }
.noicon svg { width: 18px; height: 18px; }
</style>
