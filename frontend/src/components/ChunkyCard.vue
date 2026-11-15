<template>
  <section class="card">
    <div class="card-head">
      <Boxes class="hi" /><h3>{{ $t('Generowanie świata (Chunky)') }}</h3>
      <span v-if="v?.installed" class="badge">{{ v.kind === 'mod' ? $t('mod') : $t('plugin') }} <span class="mono">{{ v.version }}</span></span>
      <span v-if="v?.update_available" class="badge amber">{{ $t('dostępna {v}', { v: v.latest_version }) }}</span>
    </div>
    <div v-if="!v" class="card-body row small muted"><Loader2 class="spin" style="width: 15px" /> {{ $t('Sprawdzanie Chunky…') }}</div>
    <div v-else class="card-body col" style="gap: 16px">
      <div class="small text-2">{{ $t('Chunky generuje świat z wyprzedzeniem, zanim gracze tam dotrą – mniej lagów przy eksploracji i gotowy teren dla mapy.') }}</div>

      <div v-if="!v.supported" class="callout small"><Info /><div>{{ v.reason }}</div></div>

      <template v-else-if="!v.installed">
        <div v-if="!v.available" class="callout warn small"><AlertTriangle /><div>{{ v.error || $t('Chunky nie ma jeszcze wersji dla tego silnika i wersji Minecrafta.') }}</div></div>
        <div class="row wrap" style="gap: 10px">
          <button class="btn primary" :disabled="busy || !v.available" @click="install">
            <Loader2 v-if="busy" class="spin" /><Download v-else />
            {{ v.kind === 'mod' ? $t('Zainstaluj mod Chunky {v}', { v: v.latest_version || '' }) : $t('Zainstaluj plugin Chunky {v}', { v: v.latest_version || '' }) }}
          </button>
          <span class="tiny muted">{{ v.kind === 'mod' ? $t('Wersja dla {e} {mc} – razem z wymaganymi bibliotekami (np. Fabric API).', { e: engineName, mc: mc }) : $t('Wersja dla {e} {mc}.', { e: engineName, mc: mc }) }}</span>
        </div>
      </template>

      <template v-else>
        <div v-if="restart" class="callout warn small">
          <RotateCcw /><div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div>
          <button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button>
        </div>

        <!-- task status -->
        <div class="task" :class="task.state">
          <div class="row" style="gap: 10px">
            <component :is="stateIcon" class="ti" />
            <div class="grow">
              <div class="small"><b>{{ $t(stateLabel) }}</b><span v-if="task.world_arg" class="muted"> · <span class="mono">{{ task.world_arg }}</span>, {{ $t('promień {r} bloków', { r: task.radius }) }}</span></div>
              <div class="tiny muted">{{ stateHint }}</div>
            </div>
            <div v-if="task.state !== 'idle'" class="pct num">{{ (task.percent || 0).toFixed(task.percent >= 100 ? 0 : 1) }}%</div>
          </div>
          <div v-if="task.state !== 'idle'" class="progress" style="margin-top: 10px"><div :style="{ width: Math.min(100, task.percent || 0) + '%' }" /></div>
          <div v-if="task.state === 'running' || task.processed" class="row wrap tiny muted" style="gap: 4px 16px; margin-top: 8px">
            <span>{{ $t('Chunki: {n}', { n: (task.processed || 0).toLocaleString() }) }}</span>
            <span v-if="task.rate && task.state === 'running'">{{ $t('Tempo: {n} chunków/s', { n: task.rate }) }}</span>
            <span v-if="task.eta && task.state === 'running'">{{ $t('Pozostało: {t}', { t: task.eta }) }}</span>
            <span v-if="task.duration">{{ $t('Czas: {t}', { t: task.duration }) }}</span>
          </div>
          <div class="row wrap" style="gap: 8px; margin-top: 12px">
            <button v-if="task.state === 'idle' || task.state === 'finished'" class="btn primary sm" :disabled="busy || dirty || !v.server_running" @click="act('start')"><Play /> {{ $t('Generuj świat') }}</button>
            <template v-else>
              <button v-if="task.state === 'paused'" class="btn sm primary" :disabled="busy" @click="act('continue')"><Play /> {{ $t('Wznów') }}</button>
              <button v-else class="btn sm" :disabled="busy" @click="act('pause')"><Pause /> {{ $t('Wstrzymaj') }}</button>
              <button class="btn sm danger" :disabled="busy" @click="cancel"><Square /> {{ $t('Anuluj') }}</button>
            </template>
            <span v-if="!v.server_running && (task.state === 'idle' || task.state === 'finished')" class="tiny muted">{{ $t('Uruchom serwer, aby zacząć.') }}</span>
            <span v-else-if="dirty" class="tiny muted">{{ $t('Najpierw zapisz ustawienia.') }}</span>
          </div>
        </div>

        <!-- selection -->
        <div class="grid grid-3">
          <div class="field">
            <label>{{ $t('Świat') }}</label>
            <select v-model="s.world" class="select">
              <option value="overworld">{{ $t('Overworld') }}</option>
              <option value="the_nether">{{ $t('Nether') }}</option>
              <option value="the_end">{{ $t('End') }}</option>
            </select>
          </div>
          <div class="field">
            <label>{{ $t('Kształt') }}</label>
            <select v-model="s.shape" class="select">
              <option v-for="sh in SHAPES" :key="sh.id" :value="sh.id">{{ $t(sh.name) }}</option>
            </select>
          </div>
          <div class="field">
            <label>{{ $t('Wzór (pattern)') }}</label>
            <select v-model="s.pattern" class="select">
              <option value="concentric">{{ $t('Koncentryczny – od środka, warstwami') }}</option>
              <option value="loop">{{ $t('Pętla – rzędami') }}</option>
              <option value="spiral">{{ $t('Spirala') }}</option>
            </select>
          </div>
        </div>
        <div class="grid grid-3">
          <div class="field">
            <label>{{ $t('Środek X') }}</label>
            <input v-model.number="s.center_x" type="number" class="input mono" />
          </div>
          <div class="field">
            <label>{{ $t('Środek Z') }}</label>
            <input v-model.number="s.center_z" type="number" class="input mono" />
          </div>
          <div class="field">
            <label>{{ $t('Promień (bloki)') }}</label>
            <input v-model.number="s.radius" type="number" min="16" step="100" class="input mono" />
          </div>
        </div>
        <div class="row wrap" style="gap: 6px">
          <span class="tiny muted">{{ $t('Szybki wybór:') }}</span>
          <button v-for="r in [1000, 2500, 5000, 10000]" :key="r" class="chip" :class="{ active: s.radius === r }" @click="s.radius = r">{{ r.toLocaleString() }}</button>
          <button class="chip" @click="s.center_x = 0; s.center_z = 0">{{ $t('środek 0, 0') }}</button>
        </div>
        <div class="tiny muted">{{ $t('Obszar ok. {area} – ok. {chunks} chunków.', { area: areaLabel, chunks: chunkEstimate.toLocaleString() }) }}</div>

        <div class="divider" />

        <Toggle v-model="s.only_when_empty" :label="$t('Generuj tylko, gdy nikt nie gra')"
                :hint="s.only_when_empty ? $t('Generowanie zatrzymuje się, gdy ktoś dołączy, i wraca minutę po wyjściu ostatniego gracza.') : $t('Generowanie działa też przy graczach – może powodować lagi.')" />

        <Toggle v-model="s.multicore" :disabled="!v.multicore.available" :label="$t('Wielowątkowe generowanie')" :hint="multicoreHint" />
        <div v-if="s.multicore && v.multicore.method === 'c2me'" class="callout warn small"><AlertTriangle /><div>{{ $t('C2ME to osobny mod przyspieszający generowanie. Rzadko, ale bywa niezgodny z innymi modami – jeśli serwer przestanie startować, wyłącz tę opcję.') }}</div></div>

        <div class="row" style="gap: 8px">
          <button class="btn sm ghost danger" :disabled="busy" @click="uninstall"><Trash2 /> {{ $t('Odinstaluj Chunky') }}</button>
          <button v-if="v.update_available" class="btn sm" :disabled="busy" @click="install"><Download /> {{ $t('Zaktualizuj') }}</button>
          <span class="spacer" />
          <button v-if="dirty" class="btn ghost sm" @click="resetSettings">{{ $t('Odrzuć') }}</button>
          <button class="btn primary sm" :disabled="busy || !dirty" @click="saveSettings"><Save /> {{ $t('Zapisz ustawienia') }}</button>
        </div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertTriangle, Boxes, CheckCircle2, Clock, Download, Info, Loader2, Pause, PauseCircle, Play, RotateCcw, Save, Square, Trash2 } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { confirmDialog, toast, toastError } from '../store'
import { ENGINE_META } from '../utils'

const props = defineProps({ serverId: String })
const v = ref(null)
const s = ref(null)
const original = ref('')
const busy = ref(false)
const restart = ref(false)
let timer = null

const SHAPES = [
  { id: 'square', name: 'Kwadrat' }, { id: 'circle', name: 'Koło' }, { id: 'rectangle', name: 'Prostokąt' },
  { id: 'ellipse', name: 'Elipsa' }, { id: 'diamond', name: 'Romb' }, { id: 'pentagon', name: 'Pięciokąt' },
  { id: 'hexagon', name: 'Sześciokąt' }, { id: 'star', name: 'Gwiazda' }, { id: 'triangle', name: 'Trójkąt' },
]

const engineName = computed(() => ENGINE_META[v.value?.engine]?.name || v.value?.engine)
const mc = computed(() => v.value?.mc_version || '')
const task = computed(() => v.value?.task || { state: 'idle' })
const dirty = computed(() => s.value && JSON.stringify(s.value) !== original.value)
const stateLabel = computed(() => ({
  idle: 'Brak zadania', running: 'Generowanie trwa', paused: 'Wstrzymane',
  waiting: 'Czeka na start', finished: 'Zakończone',
}[task.value.state] || task.value.state))
const stateIcon = computed(() => ({ running: Loader2, paused: PauseCircle, waiting: Clock, finished: CheckCircle2 }[task.value.state] || Boxes))
const stateHint = computed(() => {
  const st = task.value.state
  if (st === 'waiting') {
    if (!v.value.server_running) return t('Wystartuje, gdy serwer będzie włączony.')
    if (task.value.auto_paused || (v.value.settings.only_when_empty && v.value.players_online)) return t('Czeka, aż serwer opustoszeje – ruszy minutę po wyjściu ostatniego gracza.')
    return t('Chunky przygotowuje zadanie…')
  }
  if (st === 'paused') return t('Wstrzymane ręcznie – postęp jest zapisany.')
  if (st === 'finished') return t('Świat w zaznaczonym obszarze jest wygenerowany.')
  if (st === 'running') return v.value.settings.only_when_empty ? t('Zatrzyma się, gdy ktoś dołączy.') : t('Działa również przy graczach.')
  return t('Ustaw obszar poniżej, zapisz i kliknij „Generuj świat”.')
})
const multicoreHint = computed(() => {
  const m = v.value.multicore
  if (!m.available) return m.reason
  if (m.method === 'paper') return t('Paper generuje chunki równolegle. Domyślnie używa ok. połowy fizycznych rdzeni – ta opcja ustawia {n} wątków (wszystkie rdzenie oprócz jednego, wykryto {c}). Działa po restarcie.', { n: m.threads, c: m.cores })
  return t('Na Fabric/Quilt równoległe generowanie zapewnia mod C2ME – zostanie zainstalowany w zgodnej wersji. Działa po restarcie.')
})
const SHAPE_FACTOR = { square: 1, rectangle: 1, circle: Math.PI / 4, ellipse: Math.PI / 4, diamond: 0.5, pentagon: 0.59, hexagon: 0.65, star: 0.3, triangle: 0.33 }
const chunkEstimate = computed(() => {
  if (!s.value) return 0
  const side = (2 * (s.value.radius || 0)) / 16
  return Math.round(side * side * (SHAPE_FACTOR[s.value.shape] || 1))
})
const areaLabel = computed(() => {
  const d = 2 * (s.value?.radius || 0)
  return `${d.toLocaleString()} × ${d.toLocaleString()} ${t('bloków')}`
})

function applyState(r) {
  v.value = r
  if (!dirty.value || !s.value) {
    s.value = { ...r.settings }
    original.value = JSON.stringify(s.value)
  }
}
async function load() {
  try { applyState(await api.get(`/servers/${props.serverId}/chunky`)) } catch (e) { toastError(e) }
}
onMounted(() => {
  load()
  // Progress comes from Chunky's console output; refresh while the tab is open and visible.
  timer = setInterval(() => document.visibilityState === 'visible' && v.value?.installed && load(), 5000)
})
onUnmounted(() => clearInterval(timer))

async function install() {
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/chunky/install`)
    applyState(r)
    restart.value = !!r.restart_required
    toast(t('Zainstalowano Chunky'), 'success')
  } catch (e) { toastError(e) } finally { busy.value = false }
}
async function uninstall() {
  if (!(await confirmDialog({ title: 'Odinstalować Chunky?', message: 'Plik Chunky zostanie usunięty, a trwające generowanie anulowane. Wygenerowany świat zostaje.', confirmText: 'Odinstaluj', danger: true }))) return
  busy.value = true
  try {
    const r = await api.del(`/servers/${props.serverId}/chunky`)
    applyState(r)
    restart.value = !!r.restart_required
  } catch (e) { toastError(e) } finally { busy.value = false }
}
function resetSettings() { s.value = JSON.parse(original.value) }
async function saveSettings() {
  busy.value = true
  try {
    const r = await api.put(`/servers/${props.serverId}/chunky/settings`, s.value)
    s.value = null
    applyState(r)
    if (r.restart_required) restart.value = true
    toast(t('Zapisano ustawienia Chunky'), 'success')
  } catch (e) { toastError(e) } finally { busy.value = false }
}
async function act(action) {
  busy.value = true
  try { applyState(await api.post(`/servers/${props.serverId}/chunky/action`, { action })) } catch (e) { toastError(e) } finally { busy.value = false }
}
async function cancel() {
  if (await confirmDialog({ title: 'Anulować generowanie?', message: 'Anulowanego zadania nie da się wznowić – trzeba zacząć od nowa. Już wygenerowane chunki zostają.', confirmText: 'Anuluj zadanie', danger: true })) act('cancel')
}
async function doRestart() {
  try { await api.post(`/servers/${props.serverId}/power`, { action: 'restart' }); restart.value = false } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); flex: none; }
.task { padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
.task.running { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); }
.task.finished .ti { color: var(--accent); }
.ti { width: 18px; height: 18px; color: var(--muted); flex: none; }
.task.running .ti { color: var(--accent); animation: spin 1.6s linear infinite; }
.pct { font-size: 20px; font-weight: 650; letter-spacing: -0.02em; }
</style>
