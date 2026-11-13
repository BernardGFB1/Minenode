<template>
  <div class="col" style="gap: 16px">
    <div v-if="kind === null && engine === 'custom'" class="card empty-card">
      <Puzzle class="big" />
      <h2>{{ $t('Panel nie zarządza modami własnego silnika') }}</h2>
      <p class="muted">{{ $t('Nie wiadomo, jakie mody lub pluginy obsługuje Twój plik .jar. Wgraj je ręcznie do folderu mods/ lub plugins/ w zakładce') }}
        <router-link :to="`/servers/${id}/files`">{{ $t('Pliki') }}</router-link>.</p>
    </div>
    <div v-else-if="kind === null" class="card empty-card">
      <Puzzle class="big" />
      <h2>{{ $t('Vanilla nie obsługuje modów ani pluginów') }}</h2>
      <p class="muted">{{ $t('Zmień silnik na Paper/Purpur (pluginy) albo Fabric, Quilt, Forge lub NeoForge (mody) w') }}
        <router-link :to="`/servers/${id}/settings`">{{ $t('Ustawieniach serwera') }}</router-link>{{ $t('. Świat zostanie zachowany.') }}</p>
    </div>

    <template v-else-if="kind">
      <div class="row wrap" style="gap: 12px">
        <div class="seg">
          <button :class="{ active: view === 'installed' }" @click="view = 'installed'"><Package /> {{ $t('Zainstalowane') }} <span class="cnt">{{ items.length }}</span></button>
          <button :class="{ active: view === 'browse' }" @click="openBrowse"><Search /> {{ $t('Przeglądaj') }}</button>
        </div>
        <span class="small muted">{{ $t('{what} dla', { what: label }) }} <b class="text-2">{{ ENGINE_META[engine]?.name }} {{ mc }}</b> {{ $t('· folder') }} <span class="mono">{{ dir }}/</span></span>
        <span class="spacer" />
        <template v-if="view === 'installed'">
          <button v-if="kind === 'mod'" class="btn sm" :disabled="busy.prune" :title="$t('Wyłącza mody działające tylko u gracza (shadery, minimapy, menu…), których nie wymaga żaden inny mod')" @click="pruneClient"><Loader2 v-if="busy.prune" class="spin" /><MonitorOff v-else /> {{ $t('Wyłącz mody klienckie') }}</button>
          <button class="btn sm" :disabled="checking" @click="load(true)"><Loader2 v-if="checking" class="spin" /><ShieldCheck v-else /> {{ $t('Sprawdź zgodność') }}</button>
          <button v-if="updates.length" class="btn sm primary" :disabled="busy.all" @click="updateAll"><Loader2 v-if="busy.all" class="spin" /><ArrowUpCircle v-else /> {{ $t('Aktualizuj wszystko ({n})', { n: updates.length }) }}</button>
        </template>
      </div>

      <div v-if="restartNeeded" class="callout warn small">
        <RotateCcw /><div class="grow">{{ $t(kind === 'plugin' ? 'Zmiany w pluginach zadziałają po restarcie serwera.' : 'Zmiany w modach zadziałają po restarcie serwera.') }}</div>
        <button class="btn sm" @click="restart">{{ $t('Restartuj teraz') }}</button>
      </div>

      <!-- ===================== installed -->
      <template v-if="view === 'installed'">
        <div v-if="items.length" class="row wrap" style="gap: 8px">
          <span class="badge green"><CheckCircle2 class="bi" /> {{ $t('{n} zgodnych', { n: counts.ok }) }}</span>
          <span v-if="counts.warn" class="badge amber"><AlertTriangle class="bi" /> {{ $t('{n} niepotwierdzonych', { n: counts.warn }) }}</span>
          <span v-if="counts.bad" class="badge red"><XCircle class="bi" /> {{ $t('{n} niezgodnych', { n: counts.bad }) }}</span>
          <span v-if="counts.unknown" class="badge"><HelpCircle class="bi" /> {{ $t('{n} nieznanych', { n: counts.unknown }) }}</span>
        </div>
        <div v-if="counts.bad" class="callout warn small"><AlertTriangle /><div>{{ $t('Część plików nie jest przeznaczona dla {what} – serwer może się nie uruchomić. Zaktualizuj je, wyłącz lub usuń.', { what: `${ENGINE_META[engine]?.name} ${mc}` }) }}</div></div>
        <section class="card">
          <div v-if="loading" class="empty"><Loader2 class="spin" /></div>
          <div v-else-if="!items.length" class="empty">
            <Puzzle /><div>{{ $t(kind === 'plugin' ? 'Brak zainstalowanych pluginów.' : 'Brak zainstalowanych modów.') }}</div>
            <button class="btn primary" style="margin-top: 12px" @click="openBrowse"><Search /> {{ $t(kind === 'plugin' ? 'Znajdź pluginy' : 'Znajdź mody') }}</button>
          </div>
          <div v-else class="list">
            <div v-for="it in items" :key="it.file" class="item" :class="{ disabled: !it.enabled }">
              <img v-if="it.icon" :src="it.icon" class="ic" alt="" referrerpolicy="no-referrer" loading="lazy" />
              <div v-else class="ic ph"><Puzzle /></div>
              <div class="grow" style="min-width: 0">
                <div class="row" style="gap: 8px">
                  <b class="ellipsis">{{ it.title }}</b>
                  <span v-if="it.dependency" class="badge">{{ $t('zależność') }}</span>
                  <span v-if="!it.enabled && it.disabled_reason" class="badge amber" :title="$t(it.disabled_reason === 'crash' ? 'Wyłączony automatycznie – wysypał serwer przy starcie' : 'Wyłączony automatycznie – mod działa tylko po stronie gracza')">{{ $t(it.disabled_reason === 'crash' ? 'wysypał serwer' : 'tylko klient') }}</span>
                  <span v-else-if="!it.enabled" class="badge">{{ $t('wyłączony') }}</span>
                </div>
                <div class="tiny muted ellipsis"><span v-if="it.version_number">{{ it.version_number }} · </span><span class="mono">{{ it.file }}</span> · {{ formatBytes(it.size) }}</div>
              </div>
              <span v-if="it.compat" class="badge" :class="COMPAT[it.compat].color" :title="$tm(it.reason)">
                <component :is="COMPAT[it.compat].icon" class="bi" /> {{ $t(COMPAT[it.compat].label) }}
              </span>
              <button v-if="it.update" class="btn sm" :disabled="busy[it.file]" :title="$t('Aktualizuj do {v}', { v: it.update.version_number })" @click="update(it)">
                <Loader2 v-if="busy[it.file]" class="spin" /><ArrowUpCircle v-else /> {{ it.update.version_number.length > 18 ? $t('Aktualizuj') : it.update.version_number }}
              </button>
              <Toggle :model-value="it.enabled" @update:model-value="toggle(it, $event)" />
              <button class="btn sm ghost icon" :title="$t('Usuń')" @click="remove(it)"><Trash2 /></button>
            </div>
          </div>
        </section>
      </template>

      <!-- ===================== browse -->
      <template v-else>
        <div class="row wrap" style="gap: 10px">
          <div class="seg small-seg">
            <button :class="{ active: source === 'modrinth' }" @click="source = 'modrinth'; search(true)">Modrinth</button>
            <button :class="{ active: source === 'curseforge' }" @click="source = 'curseforge'; search(true)">CurseForge</button>
          </div>
          <div class="searchbox grow">
            <Search />
            <input v-model="q" :placeholder="kind === 'plugin' ? $t('Szukaj pluginów, np. LuckPerms, EssentialsX…') : $t('Szukaj modów, np. Lithium, Create, JEI…')" @keydown.enter="search(true)" />
          </div>
          <Toggle v-model="anyVersion" :label="$t('Wszystkie wersje')" @update:model-value="search(true)" />
        </div>
        <div v-if="source === 'curseforge' && !cfOk" class="callout warn small"><KeyRound />{{ $t('CurseForge wymaga klucza API – dodaj go w') }} <router-link to="/settings">{{ $t('Ustawieniach panelu') }}</router-link>.</div>
        <div v-else-if="error" class="callout warn small"><AlertTriangle />{{ $tm(error) }}</div>
        <div v-if="searching && !results.length" class="empty"><Loader2 class="spin" /></div>
        <div class="results">
          <article v-for="r in results" :key="r.id" class="card res">
            <img v-if="r.icon" :src="r.icon" class="ric" alt="" referrerpolicy="no-referrer" loading="lazy" />
            <div v-else class="ric ph"><Puzzle /></div>
            <div class="grow" style="min-width: 0">
              <div class="row" style="gap: 6px"><a :href="r.url" target="_blank" rel="noopener" class="title ellipsis" @click.stop>{{ r.title }}</a></div>
              <div class="tiny muted ellipsis">{{ r.author ? $t('od {who}', { who: r.author }) : '' }}</div>
              <p class="small text-2 desc">{{ r.description }}</p>
              <div class="row wrap" style="gap: 5px; margin-top: 8px">
                <span class="badge"><Download class="bi" /> {{ compact(r.downloads) }}</span>
                <span v-for="c in r.categories" :key="c" class="badge">{{ c }}</span>
                <span v-if="r.server_side === 'required'" class="badge blue">{{ $t('wymagany na serwerze') }}</span>
              </div>
            </div>
            <div class="act">
              <span v-if="r.installed" class="badge green"><Check class="bi" /> {{ $t('Zainstalowano') }}</span>
              <button v-else class="btn sm primary" :disabled="busy[r.id]" @click="install(r)"><Loader2 v-if="busy[r.id]" class="spin" /><Plus v-else /> {{ $t('Zainstaluj') }}</button>
            </div>
          </article>
        </div>
        <div v-if="results.length && results.length < total" class="row" style="justify-content: center">
          <button class="btn" :disabled="searching" @click="search(false)"><Loader2 v-if="searching" class="spin" /> {{ $t('Pokaż więcej') }}</button>
        </div>
        <div v-if="!searching && !results.length && !error && (source === 'modrinth' || cfOk)" class="empty small">{{ anyVersion ? $t('Brak wyników.') : $t('Brak wyników dla {mc} – spróbuj „Wszystkie wersje”.', { mc }) }}</div>
      </template>
    </template>
    <div v-else class="empty"><Loader2 class="spin" /></div>
  </div>
</template>
<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { MonitorOff, Puzzle, Package, Search, Loader2, ShieldCheck, ArrowUpCircle, RotateCcw, CheckCircle2, AlertTriangle, XCircle, HelpCircle, Trash2, Download, Plus, Check, KeyRound } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import { api } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { ENGINE_META, compact, formatBytes } from '../../utils'
import { t } from '../../i18n'

const live = inject('live')
const id = live.summary.id
const COMPAT = {
  ok: { label: 'Zgodny', color: 'green', icon: CheckCircle2 },
  warn: { label: 'Niepotwierdzony', color: 'amber', icon: AlertTriangle },
  bad: { label: 'Niezgodny', color: 'red', icon: XCircle },
  unknown: { label: 'Nieznany', color: '', icon: HelpCircle },
}
const kind = ref(undefined)
const engine = ref(live.summary.type)
const mc = ref(live.summary.mc_version)
const dir = ref('')
const items = ref([])
const loading = ref(true)
const checking = ref(false)
const view = ref('installed')
const busy = reactive({})
const restartNeeded = ref(false)
const source = ref('modrinth')
const q = ref('')
const anyVersion = ref(false)
const results = ref([])
const total = ref(0)
const searching = ref(false)
const error = ref('')
const cfOk = ref(true)
const label = computed(() => t(kind.value === 'plugin' ? 'Pluginy' : 'Mody'))
const counts = computed(() => items.value.reduce((a, i) => ((a[i.compat || 'unknown'] = (a[i.compat || 'unknown'] || 0) + 1), a), { ok: 0, warn: 0, bad: 0, unknown: 0 }))
const updates = computed(() => items.value.filter((i) => i.update))

async function load(check = true) {
  if (check) checking.value = true
  try {
    const d = await api.get(`/servers/${id}/content?check=${check}`)
    kind.value = d.kind
    items.value = d.items
    dir.value = d.dir
    engine.value = d.engine
    mc.value = d.mc_version || mc.value
  } catch (e) { toastError(e) } finally { loading.value = false; checking.value = false }
}
onMounted(async () => {
  load(false).then(() => load(true))
  try { cfOk.value = (await api.get('/settings')).curseforge_configured } catch {}
})

function afterChange(r) {
  if (r?.restart_required) restartNeeded.value = true
}
function openBrowse() {
  view.value = 'browse'
  if (!results.value.length) search(true)
}
async function search(reset) {
  if (source.value === 'curseforge' && !cfOk.value) { results.value = []; return }
  searching.value = true
  error.value = ''
  try {
    const off = reset ? 0 : results.value.length
    const r = await api.get(`/servers/${id}/content/search?source=${source.value}&q=${encodeURIComponent(q.value)}&offset=${off}&any_version=${anyVersion.value}`)
    results.value = reset ? r.items : [...results.value, ...r.items]
    total.value = r.total
  } catch (e) { error.value = e.message } finally { searching.value = false }
}
async function install(r) {
  busy[r.id] = true
  try {
    const res = await api.post(`/servers/${id}/content/install`, { source: r.source, project_id: r.id })
    const deps = res.installed.filter((i) => i.dependency)
    const warn = res.installed.some((i) => i.compat !== 'ok')
    toast(t('Zainstalowano {name}', { name: r.title }) + (deps.length ? ' + ' + t('{n} zależn.', { n: deps.length }) + ` (${deps.map((d) => d.title).join(', ')})` : '') + (warn ? ' – ' + t('wersja niepotwierdzona dla {mc}', { mc: mc.value }) : ''), warn ? 'info' : 'success', 5000)
    r.installed = true
    afterChange(res)
    load(true)
  } catch (e) { toastError(e) } finally { busy[r.id] = false }
}
async function update(it) {
  busy[it.file] = true
  try { afterChange(await api.post(`/servers/${id}/content/update`, { file: it.file })); toast(t('Zaktualizowano {name}', { name: it.title }), 'success'); await load(true) } catch (e) { toastError(e) } finally { busy[it.file] = false }
}
async function updateAll() {
  busy.all = true
  try { const r = await api.post(`/servers/${id}/content/update-all`); afterChange(r); toast(t('Zaktualizowano plików: {n}', { n: r.installed.length }), 'success'); await load(true) } catch (e) { toastError(e) } finally { busy.all = false }
}
async function toggle(it, enabled) {
  try { afterChange(await api.post(`/servers/${id}/content/toggle`, { file: it.file, enabled })); it.enabled = enabled } catch (e) { toastError(e) }
}
async function remove(it) {
  if (!(await confirmDialog({ title: t('Usunąć {name}?', { name: it.title }), message: it.file, confirmText: t('Usuń'), danger: true }))) return
  try { afterChange(await api.del(`/servers/${id}/content/${encodeURIComponent(it.file)}`)); items.value = items.value.filter((x) => x !== it) } catch (e) { toastError(e) }
}
async function pruneClient() {
  busy.prune = true
  try {
    const r = await api.post(`/servers/${id}/content/prune-client`)
    afterChange(r)
    toast(r.disabled.length ? t('Wyłączono modów tylko dla klienta: {n}', { n: r.disabled.length }) : t('Nie znaleziono modów tylko dla klienta.'), r.disabled.length ? 'success' : 'info')
    load(true)
  } catch (e) { toastError(e) } finally { busy.prune = false }
}
async function restart() {
  try { await api.post(`/servers/${id}/power`, { action: 'restart' }); restartNeeded.value = false } catch (e) { toastError(e) }
}
</script>
<style scoped>
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface); border: 1px solid var(--border); }
.seg button { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-weight: 500; cursor: pointer; }
.seg button svg { width: 15px; height: 15px; }
.seg button.active { background: var(--surface-3); color: var(--text); }
.cnt { font-size: 11px; background: var(--surface-3); border-radius: 10px; padding: 0 6px; }
.searchbox { display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); min-width: 220px; }
.searchbox:focus-within { border-color: var(--accent); box-shadow: var(--ring); }
.searchbox svg { width: 16px; height: 16px; color: var(--muted); }
.searchbox input { flex: 1; background: none; border: 0; outline: none; color: var(--text); font: inherit; }
.bi { width: 12px; height: 12px; }
.list { display: flex; flex-direction: column; padding: 6px; }
.item { display: flex; align-items: center; gap: 12px; padding: 9px 10px; border-radius: var(--radius-sm); }
.item:hover { background: var(--surface-2); }
.item.disabled { opacity: 0.55; }
.ic { width: 36px; height: 36px; border-radius: 8px; object-fit: cover; flex: none; background: var(--surface-3); }
.ph { display: grid; place-items: center; color: var(--muted); }
.ph svg { width: 18px; height: 18px; }
.results { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px; }
.res { display: flex; gap: 12px; padding: 14px; align-items: flex-start; }
.ric { width: 52px; height: 52px; border-radius: 10px; object-fit: cover; flex: none; background: var(--surface-3); }
.title { color: var(--text); font-weight: 600; }
.desc { margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.act { flex: none; }
.empty-card { padding: 44px 24px; text-align: center; }
.empty-card p { max-width: 520px; margin: 8px auto 0; }
.big { width: 32px; height: 32px; color: var(--muted); margin-bottom: 8px; }
.empty { display: flex; flex-direction: column; align-items: center; }
</style>
