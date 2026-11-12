<template>
  <div class="dash">
    <!-- ============================================================ STATISTICS -->
    <section class="stats-section">
      <div class="page-head">
        <div class="grow">
          <h1>{{ $t('Statystyki') }}</h1>
          <div class="sub">{{ $t('{n} skonfigurowanych · {on} online · {p} graczy', { n: fleet.servers.length, on: sys?.servers_online ?? 0, p: sys?.players_online ?? 0 }) }}</div>
        </div>
      </div>

      <div class="grid grid-4">
        <div v-for="m in metrics" :key="m.key" class="card stat">
          <div class="stat-label"><component :is="m.icon" /> {{ $t(m.label) }}</div>
          <div class="stat-value num">{{ m.value }}<small v-if="m.unit">{{ $t(m.unit) }}</small></div>
          <Meter v-if="m.meter != null" :value="m.meter" />
          <div v-else class="row tiny" :style="{ color: m.ok ? 'var(--muted)' : 'var(--danger)' }"><span class="dot" :style="{ background: m.ok ? 'var(--accent)' : 'var(--danger)' }" />{{ $tm(m.status) }}</div>
          <div class="tiny muted">{{ $tm(m.hint) }}</div>
        </div>
      </div>
    </section>

    <!-- ============================================================ SERVERS -->
    <section class="servers-section">
      <div class="servers-head">
        <h2>{{ $t('Serwery') }}</h2>
        <span class="badge">{{ fleet.servers.length }}</span>
        <div class="seg" role="tablist">
          <button v-for="f in FILTERS" :key="f.id" :class="{ active: filter === f.id }" @click="filter = f.id">
            {{ $t(f.label) }} <span class="cnt">{{ counts[f.id] }}</span>
          </button>
        </div>
        <div class="searchbox">
          <Search /><input v-model="query" :placeholder="$t('Szukaj serwera…')" />
        </div>
        <span class="spacer" />
        <div class="seg icons">
          <button :class="{ active: view === 'grid' }" :title="$t('Kafelki')" @click="view = 'grid'"><LayoutGrid /></button>
          <button :class="{ active: view === 'list' }" :title="$t('Lista')" @click="view = 'list'"><List /></button>
        </div>
        <button class="btn" @click="$refs.packInput.click()"><Upload /> {{ $t('Importuj .minenodepack') }}</button>
        <input ref="packInput" type="file" accept=".minenodepack" hidden @change="importPack" />
        <router-link to="/modpacks" class="btn"><Package /> {{ $t('Importuj modpack') }}</router-link>
        <router-link to="/new" class="btn primary"><Plus /> {{ $t('Nowy serwer') }}</router-link>
      </div>

      <div v-if="fleet.loaded && !fleet.servers.length" class="onboarding">
        <Boxes class="big-icon" />
        <h2>{{ $t('Utwórz swój pierwszy serwer') }}</h2>
        <p class="muted">{{ $t('Wybierz silnik (Vanilla, Paper, Fabric, Forge…) i wersję – panel pobierze pliki serwera i właściwą Javę automatycznie.') }}</p>
        <div class="row" style="justify-content: center; margin-top: 18px">
          <router-link to="/modpacks" class="btn"><Package /> {{ $t('Z modpacka') }}</router-link>
          <router-link to="/new" class="btn primary"><Plus /> {{ $t('Nowy serwer') }}</router-link>
        </div>
      </div>
      <div v-else-if="fleet.loaded && !shown.length" class="empty small">{{ $t('Brak serwerów pasujących do filtra.') }}</div>

      <div v-if="view === 'grid'" class="servers">
        <ServerCard v-for="s in shown" :key="s.id" :s="s" :now="now" @power="(a) => power(s, a)" @copy="copy" />
      </div>
      <div v-else class="card rows">
        <div v-for="s in shown" :key="s.id" class="srow" :class="`tone-${stateTone(s.state)}`" @click="$router.push(`/servers/${s.id}`)">
          <ServerAvatar :server="s" :size="36" />
          <div class="grow" style="min-width: 0">
            <b class="ellipsis">{{ s.name }}</b>
            <div class="tiny muted ellipsis">{{ ENGINE_META[s.type]?.name }} {{ s.mc_version !== '?' ? s.mc_version : '' }} · <span class="mono">{{ $addr(s.address) }}</span></div>
          </div>
          <StatusBadge :state="s.state" />
          <span class="col-m num small"><b>{{ s.players_online }}</b><span class="muted">/{{ s.max_players }}</span></span>
          <span class="col-m num small">{{ s.state === 'online' ? Math.round(s.cpu) + '%' : '–' }}</span>
          <div class="col-ram">
            <div class="tiny muted num" style="text-align: right">{{ s.mem ? formatBytes(s.mem) : '–' }} / {{ formatMB(s.max_ram_mb) }}</div>
            <Meter :value="s.mem / (s.max_ram_mb * 1048576 * 1.15)" />
          </div>
          <div class="row" style="gap: 4px" @click.stop>
            <button v-if="['offline', 'crashed'].includes(s.state)" class="btn sm icon" :title="$t('Start')" :disabled="!s.installed" @click="power(s, 'start')"><Play /></button>
            <button v-else-if="s.state === 'online' || s.state === 'starting'" class="btn sm icon" :title="$t('Stop')" @click="power(s, 'stop')"><Square /></button>
            <router-link :to="`/servers/${s.id}/console`" class="btn sm ghost icon" :title="$t('Konsola')"><TerminalSquare /></router-link>
          </div>
        </div>
      </div>
    </section>

  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Plus, Package, Upload, Cpu, MemoryStick, HardDrive, Network, Boxes, Search, LayoutGrid, List, Play, Square, TerminalSquare } from 'lucide-vue-next'
import Meter from '../components/Meter.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ServerAvatar from '../components/ServerAvatar.vue'
import ServerCard from '../components/ServerCard.vue'
import { api } from '../api'
import { fleet, refreshFleet } from '../servers'
import { toast, toastError } from '../store'
import { ENGINE_META, compact, copyText, formatBytes, formatMB, stateTone } from '../utils'

const read = (k, d) => { try { return localStorage.getItem(k) || d } catch { return d } }
const view = ref(read('minenode-dash-view', 'grid'))

const sys = ref(null)
const now = ref(Date.now() / 1000)
const filter = ref('all')
const query = ref('')
const FILTERS = [{ id: 'all', label: 'Wszystkie' }, { id: 'online', label: 'Online' }, { id: 'offline', label: 'Wyłączone' }]
const isOnline = (s) => ['online', 'starting', 'stopping'].includes(s.state)
const counts = computed(() => ({
  all: fleet.servers.length,
  online: fleet.servers.filter(isOnline).length,
  offline: fleet.servers.filter((s) => !isOnline(s)).length,
}))
const shown = computed(() => fleet.servers.filter((s) =>
  (filter.value === 'all' || (filter.value === 'online') === isOnline(s)) &&
  (!query.value || `${s.name} ${s.address} ${s.type}`.toLowerCase().includes(query.value.toLowerCase()))))

const metrics = computed(() => {
  const s = sys.value
  return [
    { key: 'cpu', icon: Cpu, label: 'Procesor', value: s ? Math.round(s.cpu_percent) : '–', unit: '%', meter: (s?.cpu_percent || 0) / 100, hint: s ? `${s.cpu_count} rdzeni · ${s.platform}` : '' },
    { key: 'mem', icon: MemoryStick, label: 'Pamięć', value: s ? formatBytes(s.mem_used) : '–', meter: s ? s.mem_used / s.mem_host_total : 0, hint: s ? `z ${formatBytes(s.mem_total)}${s.container_limit ? ' (limit kontenera)' : ''}` : '' },
    { key: 'disk', icon: HardDrive, label: 'Dysk danych', value: s ? formatBytes(s.disk_used) : '–', meter: s ? s.disk_used / s.disk_total : 0, hint: s ? `wolne ${formatBytes(s.disk_free)}` : '' },
    { key: 'proxy', icon: Network, label: 'Proxy', value: s?.proxy?.active ?? 0, unit: ' połączeń', meter: null, ok: !!s?.proxy?.listening,
      status: s?.proxy?.listening ? `nasłuchuje na porcie ${s.proxy.port}` : s?.proxy?.error || 'nie działa',
      hint: `łącznie ${compact(s?.proxy?.total)} · ↓ ${formatBytes(s?.proxy?.bytes_down)}` },
  ]
})

let t
async function loadSys() { try { sys.value = await api.get('/system') } catch {} }
onMounted(() => {
  loadSys()
  t = setInterval(() => { now.value = Date.now() / 1000; if (document.visibilityState === 'visible') loadSys() }, 5000)
})
onUnmounted(() => clearInterval(t))
const saveView = () => { try { localStorage.setItem('minenode-dash-view', view.value) } catch {} }
watch(view, saveView)

async function power(s, action) {
  try { await api.post(`/servers/${s.id}/power`, { action }); refreshFleet() } catch (e) { toastError(e) }
}
async function copy(text) { if (await copyText(text)) toast('Skopiowano adres', 'success', 1800) }
async function importPack(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  try {
    await api.upload('/servers/import', form)
    toast('Serwer zaimportowany', 'success')
    refreshFleet()
  } catch (e) { toastError(e) }
}
</script>
<style scoped>
.dash { min-height: 100vh; display: flex; flex-direction: column; }
.stats-section { padding: 28px 32px 0; max-width: 1320px; width: 100%; margin: 0 auto; }
.servers-section { flex: 1; display: flex; flex-direction: column; gap: 18px; }
.stat-label { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-2); font-weight: 550; }
.stat-label svg { width: 15px; height: 15px; color: var(--muted); }
.stat { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.stat-value { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.stat-value small { font-size: 13px; color: var(--muted); font-weight: 500; }
.servers { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; align-content: start; }
.servers-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.servers-head h2 { font-size: 18px; }
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface); border: 1px solid var(--border); margin-left: 8px; }
.seg button { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 11px; border: 0; border-radius: 6px; background: none; color: var(--text-2); font: inherit; font-size: 13px; font-weight: 500; cursor: pointer; }
.seg button svg { width: 15px; height: 15px; }
.seg button.active { background: var(--surface-3); color: var(--text); }
.seg.icons { margin-left: 0; }
.seg.icons button { padding: 0 8px; }
.cnt { font-size: 11px; color: var(--muted); }
.searchbox { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 11px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); width: 220px; }
.searchbox:focus-within { border-color: var(--accent); box-shadow: var(--ring); }
.searchbox svg { width: 15px; height: 15px; color: var(--muted); }
.searchbox input { flex: 1; min-width: 0; background: none; border: 0; outline: none; color: var(--text); font: inherit; font-size: 13px; }
.onboarding { padding: 40px 24px; text-align: center; }
.onboarding p { max-width: 480px; margin: 8px auto 0; }
.big-icon { width: 36px; height: 36px; color: var(--accent); margin-bottom: 10px; }

/* servers: full-width work area below the statistics */
.stats-section { padding-bottom: 28px; }
.servers-section { background: var(--bg-sunken); border-top: 1px solid var(--border); padding: 22px 32px 40px; }
.servers-section > * { max-width: 1320px; width: 100%; margin-left: auto; margin-right: auto; }
.rows { display: flex; flex-direction: column; padding: 6px; }
.srow { display: flex; align-items: center; gap: 14px; padding: 9px 12px; border-radius: var(--radius-sm); cursor: pointer; border-left: 3px solid transparent; }
.srow.tone-on { border-left-color: var(--accent); }
.srow.tone-off { border-left-color: color-mix(in srgb, var(--danger) 45%, var(--border)); }
.srow.tone-busy { border-left-color: var(--warn); }
.srow:hover { background: var(--surface-2); }
.col-m { width: 54px; text-align: right; flex: none; }
.col-ram { width: 150px; flex: none; display: flex; flex-direction: column; gap: 5px; }

@media (max-width: 1000px) {
  .col-ram { display: none; }
}
@media (max-width: 720px) {
  .stats-section { padding: 18px 16px 0; }
  .servers-section { padding: 18px 16px 32px; }
  .col-m { display: none; }
}
</style>
