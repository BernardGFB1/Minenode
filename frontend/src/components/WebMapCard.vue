<template>
  <section class="card">
    <div class="card-head">
      <MapIcon class="hi" /><h3>{{ $t('Mapa świata na żywo') }}</h3>
      <span v-if="v && v.enabled && v.map_available" class="badge green">{{ $t('na stronie') }}</span>
      <span v-else-if="v && v.enabled && cur.proxy && !v.running" class="badge">{{ $t('gdy serwer działa') }}</span>
      <span v-else-if="v && v.enabled" class="badge amber">{{ $t('oczekuje na start') }}</span>
      <span v-else-if="v" class="badge">{{ $t('opcjonalna') }}</span>
    </div>
    <div class="card-body col" style="gap: 16px">
      <div v-if="!v" class="row small muted"><Loader2 class="spin" style="width: 15px" /> {{ $t('Sprawdzanie mapy…') }}</div>
      <template v-else>
        <div class="row" style="align-items: flex-start; gap: 12px">
          <MapIcon class="hi" style="margin-top: 2px" />
          <div class="grow col" style="gap: 2px">
            <b class="small">{{ $t('Mapa świata na żywo') }}</b>
            <div class="tiny muted">{{ $t('Dodaje do strony interaktywną mapę. Panel sam instaluje, aktualizuje i konfiguruje wybraną mapę.') }}</div>
          </div>
          <Toggle :model-value="v.enabled" :disabled="busy || !cur.supported || (!v.enabled && cur.available === false)" @update:model-value="(on) => save({ enabled: on })" />
        </div>

        <div class="providers" role="radiogroup" :aria-label="$t('Rodzaj mapy')">
          <button v-for="p in v.providers" :key="p.id" type="button" role="radio" class="prov" :aria-checked="p.id === v.provider"
                  :class="{ on: p.id === v.provider, off: !p.supported }" :disabled="busy || !p.supported || renderRunning" :title="p.reason || ''"
                  @click="pick(p)">
            <span class="row" style="gap: 6px"><b>{{ p.title }}</b><span class="badge">{{ p.three_d ? '2D / 3D' : '2D' }}</span></span>
            <span class="tiny muted">{{ p.supported ? $t(p.about) : $t(p.reason) }}</span>
          </button>
        </div>

        <div v-if="!cur.supported" class="callout small"><Info /><div>{{ $t(cur.reason) }}</div></div>
        <div v-else-if="cur.available === false && !v.enabled" class="callout warn small">
          <AlertTriangle /><div>{{ cur.error || $t('Brak wersji tej mapy dla tego silnika i wersji Minecrafta.') }}</div>
        </div>
        <div v-else-if="cur.compat === 'warn'" class="callout warn small">
          <AlertTriangle /><div>{{ $t('{m} nie ma wersji oznaczonej dla Minecrafta {v} – panel użyje najbliższej. Powinna działać, ale nie jest to potwierdzone.', { m: cur.title, v: v.mc_version }) }}</div>
        </div>
        <div v-if="v.provider === 'bluemap' && !v.enabled && cur.supported" class="callout info small"><Info /><div>{{ $t('BlueMap pobiera pliki klienta Minecrafta z serwerów Mojang, żeby narysować bloki. Włączając BlueMap, akceptujesz EULA Minecrafta (minecraft.net/eula).') }}</div></div>

        <template v-if="cur.supported">
          <Toggle :model-value="v.show_players" :disabled="busy" :label="$t('Pokazuj pozycje graczy')"
                  :hint="$t('Gracze online są widoczni na mapie na żywo. Wyłącz, jeśli pozycje mają zostać tajemnicą.')" @update:model-value="(on) => save({ show_players: on })" />
          <div v-if="cur.three_d" class="row wrap" style="gap: 10px">
            <span class="small">{{ $t('Widok') }}</span>
            <div class="seg">
              <button type="button" :class="{ active: !v.render_3d }" :disabled="busy" @click="save({ render_3d: false })">{{ $t('Tylko 2D') }}</button>
              <button type="button" :class="{ active: v.render_3d }" :disabled="busy" @click="save({ render_3d: true })">2D + 3D</button>
            </div>
            <span class="tiny muted">{{ $t(viewHint) }}</span>
          </div>
        </template>

        <template v-if="v.enabled">
          <div class="render" :class="{ running: renderRunning }">
            <div class="row" style="gap: 12px; align-items: center">
              <div class="grow col" style="gap: 2px">
                <b class="small">{{ $t('Generowanie mapy') }}</b>
                <span class="tiny muted">{{ renderText }}</span>
              </div>
              <div v-if="renderRunning && renderPercent !== null" class="pct num">{{ renderPercentLabel }}</div>
              <button v-if="renderRunning" class="btn sm" :disabled="!!renderBusy" @click="render('cancel')"><Square /> {{ $t('Przerwij') }}</button>
              <template v-else>
                <button v-if="canUpdate" class="btn sm" :disabled="!!renderBusy || !v.online"
                        :title="v.online ? $t('Sprawdza całą mapę i przerysowuje tylko zmienione fragmenty.') : $t('Uruchom serwer, żeby wygenerować mapę')"
                        @click="render('start', 'update')">
                  <Loader2 v-if="renderBusy === 'update'" class="spin" /><RefreshCw v-else /> {{ $t('Aktualizuj zmienione') }}</button>
                <button class="btn sm" :disabled="!!renderBusy || !v.online"
                        :title="v.online ? (canUpdate ? $t('Przerysowuje wszystkie kafelki od nowa – przydaje się po zmianie widoku.') : '') : $t('Uruchom serwer, żeby wygenerować mapę')"
                        @click="render('start', 'full')">
                  <Loader2 v-if="renderBusy === 'full'" class="spin" /><Play v-else />
                  {{ canUpdate ? $t('Wygeneruj od nowa') : $t('Wygeneruj mapę') }}</button>
              </template>
            </div>
            <div v-if="renderRunning" class="progress" :class="{ indeterminate: renderPercent === null }">
              <div :style="renderPercent !== null ? { width: Math.min(100, renderPercent) + '%' } : undefined" />
            </div>
            <div v-if="renderRunning && (renderWorldsLabel || renderCountLabel || renderRateLabel || renderEtaLabel || renderPaused)"
                 class="row wrap tiny muted" style="gap: 4px 16px">
              <span v-if="renderWorldsLabel">{{ renderWorldsLabel }}</span>
              <span v-if="renderCountLabel">{{ renderCountLabel }}</span>
              <span v-if="renderRateLabel">{{ renderRateLabel }}</span>
              <span v-if="renderEtaLabel">{{ renderEtaLabel }}</span>
              <span v-if="renderPaused">{{ $t('BlueMap wstrzymał renderowanie, dopóki na serwerze są gracze.') }}</span>
            </div>
            <div v-if="renderNoPercent" class="tiny muted">{{ $t('Ta mapa nie podaje procentu – postęp widać po liczbie kafelków.') }}</div>
          </div>

          <dl class="info">
            <dt>{{ $t('Wersja') }}</dt>
            <dd>
              <span class="mono">{{ cur.version || '—' }}</span>
              <span v-if="cur.update_available" class="badge amber" style="margin-left: 8px">{{ $t('dostępna {v}', { v: cur.latest_version }) }}</span>
            </dd>
            <dt>{{ $t('Pliki mapy') }}</dt>
            <dd class="mono">{{ v.data_dir }}/</dd>
          </dl>

          <div class="row wrap" style="gap: 8px">
            <a v-if="v.map_available" :href="v.map_url" target="_blank" rel="noopener" class="btn sm"><MapIcon /> {{ $t('Otwórz samą mapę') }}</a>
            <button v-if="cur.update_available" class="btn sm" :disabled="busy" @click="save({ update: true })">
              <Loader2 v-if="busy" class="spin" /><Download v-else /> {{ $t('Zaktualizuj teraz') }}
            </button>
          </div>
        </template>

        <div v-if="restart" class="callout warn small">
          <RotateCcw />
          <div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div>
          <button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button>
        </div>
        <div v-else-if="v.enabled && !v.map_available && !(cur.proxy && !v.running)" class="callout info small">
          <Info /><div>{{ $t('Mapa pojawi się po uruchomieniu serwera z nową mapą. Pierwsze kafelki mogą potrzebować chwili.') }}</div>
        </div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertTriangle, Download, Info, Loader2, Map as MapIcon, Play, RefreshCw, RotateCcw, Square } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { confirmDialog, toast, toastError } from '../store'

const props = defineProps({ serverId: String })
const v = ref(null)
const busy = ref(false)
const restart = ref(false)
const renderBusy = ref('')  // '', 'update', 'full' or 'cancel'
let timer = null

const cur = computed(() => v.value?.current || {})
const renderState = computed(() => v.value?.render || null)
const canUpdate = computed(() => !!cur.value.incremental)
const renderRunning = computed(() => !!renderState.value?.running)
const renderPercent = computed(() => typeof renderState.value?.percent === 'number' ? renderState.value.percent : null)
const renderPercentLabel = computed(() => renderPercent.value === null ? ''
  : `${renderPercent.value.toFixed(renderPercent.value >= 100 ? 0 : 1)}%`)
const renderWorldsLabel = computed(() => {
  const r = renderState.value
  if (!r?.running || (r.worlds_total || 0) <= 1) return ''
  const done = Math.min(r.worlds_done || 0, r.worlds_total)
  const index = Math.min(done + 1, r.worlds_total)
  return t('Świat {a} z {b}', { a: index, b: r.worlds_total })
})
const renderCountLabel = computed(() => {
  const r = renderState.value
  if (r?.processed == null) return ''
  const unit = r.unit === 'tiles' ? t('Kafelki') : t('Chunki')
  const done = Number(r.processed).toLocaleString()
  const total = r.total != null ? ' / ' + Number(r.total).toLocaleString() : ''
  return `${unit}: ${done}${total}`
})
const renderRateLabel = computed(() => {
  const r = renderState.value
  if (!r?.rate) return ''
  const n = Number(r.rate).toLocaleString()
  return r.unit === 'tiles' ? t('Tempo: {n} kafelków/s', { n }) : t('Tempo: {n} chunków/s', { n })
})
const renderEtaLabel = computed(() => renderState.value?.eta ? t('Pozostało: {t}', { t: renderState.value.eta }) : '')
const renderPaused = computed(() => renderRunning.value && !!renderState.value?.paused)
const renderNoPercent = computed(() => renderRunning.value && renderPercent.value === null)
const viewHint = computed(() => v.value?.provider === 'bluemap'
  ? (v.value.render_3d ? 'Modele 3D bloków – najładniej, ale renderowanie trwa dłużej i zajmuje więcej miejsca.' : 'Tylko płaski widok z góry – szybkie renderowanie i mało miejsca na dysku.')
  : (v.value?.render_3d ? 'Mapa płaska i izometryczna (ukośny widok terenu).' : 'Tylko płaska mapa – najmniej pracy dla serwera.'))
const renderText = computed(() => {
  const r = renderState.value
  if (r?.running) {
    const world = worldLabel(r.current)
    const left = r.queue?.length || 0
    const what = r.mode === 'update' ? t('Aktualizowanie zmienionych fragmentów mapy.') : ''
    if (world && left) return [what, t('Teraz renderuje się {w}. Kolejne światy: {n}.', { w: world, n: left })].filter(Boolean).join(' ')
    if (world) return [what, t('Teraz renderuje się {w}.', { w: world })].filter(Boolean).join(' ')
    return [what, t('Przygotowywanie renderowania…')].filter(Boolean).join(' ')
  }
  const date = r?.finished_at ? new Date(r.finished_at * 1000).toLocaleString() : ''
  const done = date ? ' ' + t(r?.mode === 'update' ? 'Ostatnio zaktualizowano: {d}.' : 'Ostatnio: {d}.', { d: date }) : ''
  if (v.value?.provider === 'bluemap') {
    return t('BlueMap sam dorysowuje nowe i zmienione chunki. „Aktualizuj zmienione” sprawdza całą mapę i przerysowuje tylko zmienione fragmenty, a „Wygeneruj od nowa” liczy wszystkie kafelki na nowo – przydaje się po zmianie widoku.') + done
  }
  return t('Nowe i zmienione chunki pojawiają się na mapie same. Teren sprzed włączenia mapy dorysuje pełny render – uruchamiasz go tylko tym przyciskiem.') + done
})

function worldLabel(id) {
  if (!id) return ''
  const s = String(id).toLowerCase()
  if (s === 'dim-1' || s.includes('nether')) return t('Nether')
  if (s === 'dim1' || s.includes('the_end') || s.endsWith('_end')) return t('End')
  if (s === 'world' || s.includes('overworld')) return t('Overworld')
  return id
}

async function load() {
  try { v.value = await api.get(`/servers/${props.serverId}/webmap`) } catch (e) { toastError(e) }
}
onMounted(() => {
  load()
  timer = setInterval(() => { if (renderRunning.value) load() }, 5000)
})
onUnmounted(() => clearInterval(timer))

async function save(patch) {
  const was = v.value
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/webmap`, patch)
    v.value = r
    if (r.restart_required) restart.value = true
    if (patch.enabled === true) toast(t('Mapa {m} gotowa', { m: r.current.title }), 'success')
    else if (patch.enabled === false) toast(t('Wyłączono mapę – strona ze statystykami działa dalej'), 'success')
    else if (patch.provider && was.enabled) toast(t('Wybrano mapę {m}', { m: r.current.title }), 'success')
    else if (patch.update) toast(t('Zaktualizowano mapę'), 'success')
  } catch (e) { toastError(e) } finally { busy.value = false }
}

async function pick(p) {
  if (p.id === v.value.provider) return
  if (v.value.enabled && !(await confirmDialog({
    title: t('Zmienić mapę na {m}?', { m: p.title }),
    message: t('Panel zainstaluje {m} i wyłączy obecną mapę (jej pliki zostaną na dysku). Nowa mapa zacznie się rysować od zera po restarcie serwera.', { m: p.title })
      + (p.id === 'bluemap' ? ' ' + t('BlueMap pobiera pliki klienta Minecrafta z serwerów Mojang, żeby narysować bloki. Włączając BlueMap, akceptujesz EULA Minecrafta (minecraft.net/eula).') : ''),
    confirmText: t('Zmień mapę'),
  }))) return
  save({ provider: p.id })
}

async function render(action, mode = 'full') {
  renderBusy.value = action === 'start' ? mode : action
  try {
    v.value = await api.post(`/servers/${props.serverId}/webmap/render`, { action, mode })
    if (action === 'start') {
      toast(mode === 'update' ? t('Aktualizacja zmienionych fragmentów rozpoczęta') : t('Renderowanie mapy rozpoczęte'), 'success')
    }
  } catch (e) { toastError(e) } finally { renderBusy.value = '' }
}

async function doRestart() {
  try {
    await api.post(`/servers/${props.serverId}/power`, { action: 'restart' })
    restart.value = false
  } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); flex: none; }
.info { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.callout:not(.warn):not(.info) { background: var(--surface-2); }
.providers { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.prov { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; padding: 10px 12px; text-align: left;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
.prov:hover:not(:disabled) { border-color: var(--border-strong); }
.prov.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.prov:disabled { cursor: not-allowed; }
.prov.off { opacity: 0.6; }
.prov b { font-size: 13px; }
.render { display: flex; flex-direction: column; align-items: stretch; gap: 10px; padding: 10px 12px; border: 1px solid transparent; border-radius: var(--radius-sm); background: var(--surface-2); }
.render.running { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); }
.pct { font-size: 20px; font-weight: 650; letter-spacing: -0.02em; flex: none; }
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface-2); border: 1px solid var(--border); }
.seg button { height: 26px; padding: 0 12px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-size: 13px; font-weight: 500; cursor: pointer; }
.seg button.active { background: var(--surface-3); color: var(--text); }
@media (max-width: 700px) { .providers { grid-template-columns: 1fr; } }
</style>
