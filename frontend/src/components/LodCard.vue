<template>
  <section class="card">
    <div class="card-head">
      <Mountain class="hi" /><h3>{{ $t('Daleki teren (LOD)') }}</h3>
      <span v-if="anyOn" class="badge green">{{ $t('włączony') }}</span>
    </div>
    <div class="card-body col" style="gap: 14px">
      <div class="small text-2">{{ $t('Serwer wysyła graczom uproszczony teren daleko poza zasięg widzenia, więc od razu widzą góry i wybrzeża setki chunków dalej – bez zwiedzania świata. Gracz musi mieć odpowiedni mod u siebie.') }}</div>
      <div v-if="!d" class="row small muted"><Loader2 class="spin" style="width: 15px" /> {{ $t('Sprawdzanie…') }}</div>
      <template v-else>
        <div v-for="it in items" :key="it.key" class="addon">
          <div class="grow col" style="gap: 3px; min-width: 0">
            <div class="row wrap" style="gap: 8px">
              <b class="small">{{ it.label }}</b>
              <span class="badge">{{ $t(it.kind === 'plugin' ? 'plugin' : 'mod') }} · {{ it.title }}</span>
              <span v-if="it.version" class="badge mono">{{ it.version }}</span>
              <span v-if="it.update_available" class="badge amber">{{ $t('dostępna {v}', { v: it.latest_version }) }}</span>
            </div>
            <div class="tiny muted">{{ $t(it.client) }}</div>
            <div v-if="!it.supported" class="tiny warn-text">{{ $t(it.reason) }}</div>
            <div v-else-if="it.available === false && !it.enabled" class="tiny warn-text">{{ it.error || $t('Brak wersji dla tego silnika i wersji Minecrafta.') }}</div>
            <div v-else-if="it.compat === 'warn'" class="tiny warn-text">{{ $t('Brak wersji oznaczonej dla Minecrafta {v} – panel użyje najbliższej.', { v: d.mc_version }) }}</div>
          </div>
          <Toggle :model-value="it.enabled" :disabled="busy || !it.supported || (!it.enabled && it.available === false)" @update:model-value="(on) => save({ [it.key]: on })" />
        </div>

        <template v-if="anyOn">
          <div class="divider" />
          <div class="row wrap" style="gap: 10px">
            <span class="small">{{ $t('Zasięg') }}</span>
            <div class="seg">
              <button v-for="n in DISTANCES" :key="n" type="button" :class="{ active: d.distance === n }" :disabled="busy" @click="save({ distance: n })">{{ n }}</button>
            </div>
            <span class="tiny muted">{{ $t('chunków (≈ {b} bloków). Więcej = więcej pracy dla serwera i łącza.', { b: d.distance * 16 }) }}</span>
          </div>
          <Toggle :model-value="d.generate" :disabled="busy" :label="$t('Generuj brakujący teren')"
                  :hint="$t('Wyłączone: LOD powstają tylko z już istniejących chunków – najlżej dla serwera. Włączone: serwer generuje nowe chunki na prośbę graczy, co mocno obciąża procesor. Taniej jest raz wygenerować świat Chunky (karta niżej).')"
                  @update:model-value="(on) => save({ generate: on })" />
          <div class="callout info small"><Gauge /><div>{{ $t('Panel ustawia oszczędne limity: 2 wątki robocze, kilka zapytań naraz na gracza i ograniczony transfer (DH ~300 KB/s, Voxy 2 MiB/s na gracza). Distant Horizons przyjmuje zasięg minimum 256 chunków.') }}</div></div>
        </template>

        <div v-if="restart" class="callout warn small">
          <RotateCcw />
          <div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div>
          <button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button>
        </div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { Gauge, Loader2, Mountain, RotateCcw } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { toast, toastError } from '../store'

const props = defineProps({ serverId: String })
const DISTANCES = [64, 128, 256, 512]
const d = ref(null)
const busy = ref(false)
const restart = ref(false)

const LABELS = {
  distant_horizons: { label: 'Distant Horizons', client: 'Gracze instalują mod Distant Horizons (Fabric, NeoForge lub Forge).' },
  voxy: { label: 'Voxy', client: 'Gracze instalują Voxy oraz LOD Server Support lub Voxy Server Side – to ten sam mod pod dwiema nazwami (Fabric; na części wersji także NeoForge).' },
}
const items = computed(() => Object.entries(d.value?.items || {}).map(([key, it]) => ({ key, ...it, ...LABELS[key] })))
const anyOn = computed(() => items.value.some((it) => it.enabled))

async function load() {
  try { d.value = await api.get(`/servers/${props.serverId}/lod`) } catch (e) { toastError(e) }
}
onMounted(load)

async function save(patch) {
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/lod`, patch)
    d.value = r
    if (r.restart_required) restart.value = true
    for (const [key, on] of Object.entries(patch)) {
      if (key in LABELS) toast(t(on ? '{m} włączony' : '{m} wyłączony', { m: LABELS[key].label }), 'success')
    }
  } catch (e) { toastError(e) } finally { busy.value = false }
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
.addon { display: flex; align-items: center; gap: 12px; }
.warn-text { color: var(--warn); }
.seg { display: inline-flex; padding: 3px; border-radius: 9px; background: var(--surface-2); border: 1px solid var(--border); }
.seg button { height: 26px; padding: 0 10px; border: 0; border-radius: 7px; background: none; color: var(--text-2); font: inherit; font-size: 13px; font-weight: 500; cursor: pointer; font-variant-numeric: tabular-nums; }
.seg button.active { background: var(--surface-3); color: var(--text); }
</style>
