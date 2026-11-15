<template>
  <section class="card">
    <div class="card-head">
      <BarChart3 class="hi" /><h3>{{ $t('Publiczna strona serwera') }}</h3>
      <a :href="pageUrl" target="_blank" rel="noopener" class="btn sm ghost"><ExternalLink /> {{ $t('Otwórz') }}</a>
    </div>
    <div class="card-body col" style="gap: 16px">
      <div class="col" style="gap: 8px">
        <div class="small text-2">{{ $t('Strona ze statystykami jest zawsze dostępna – bez logowania. Wyślij link graczom: zobaczą, kto gra, kto ostatnio był online i jak dołączyć.') }}</div>
        <div class="input-group">
          <input class="input mono" :value="fullUrl" readonly @focus="$event.target.select()" />
          <button class="btn addon-btn" :title="$t('Kopiuj link')" @click="copyLink"><Copy /></button>
        </div>
      </div>

      <div class="divider" />

      <div v-if="!v" class="row small muted"><Loader2 class="spin" style="width: 15px" /> {{ $t('Sprawdzanie squaremap…') }}</div>
      <template v-else>
        <div class="row" style="align-items: flex-start; gap: 12px">
          <MapIcon class="hi" style="margin-top: 2px" />
          <div class="grow col" style="gap: 2px">
            <div class="row" style="gap: 8px">
              <b class="small">{{ $t('Mapa świata (squaremap)') }}</b>
              <span v-if="v.enabled && v.map_available" class="badge green">{{ $t('na stronie') }}</span>
              <span v-else-if="v.enabled" class="badge amber">{{ $t('oczekuje na start') }}</span>
              <span v-else class="badge">{{ $t('opcjonalna') }}</span>
            </div>
            <div class="tiny muted">{{ $t('Dodaje do strony interaktywną mapę na żywo. Panel sam instaluje i aktualizuje squaremap.') }}</div>
          </div>
          <Toggle :model-value="v.enabled" :disabled="busy || !v.supported || (!v.enabled && !v.available)" @update:model-value="toggle" />
        </div>

        <div v-if="!v.supported" class="callout small">
          <Info />
          <div>{{ $t('Mapa wymaga silnika z pluginami lub modami (Paper, Purpur, Fabric, Quilt, Forge, NeoForge). Strona ze statystykami działa bez niej.') }}</div>
        </div>
        <div v-else-if="!v.available && !v.enabled" class="callout warn small">
          <AlertTriangle /><div>{{ v.error || $t('Brak wersji squaremap dla tego silnika i wersji Minecrafta.') }}</div>
        </div>

        <template v-if="v.enabled">
          <Toggle :model-value="v.idle_render" :disabled="busy" :label="$t('Renderuj mapę, gdy nikt nie gra')"
                  :hint="renderHint" @update:model-value="setIdleRender" />

          <dl class="info">
            <dt>{{ $t('Wersja') }}</dt>
            <dd>
              <span class="mono">{{ v.version || '—' }}</span>
              <span v-if="v.update_available" class="badge amber" style="margin-left: 8px">{{ $t('dostępna {v}', { v: v.latest_version }) }}</span>
            </dd>
            <dt>{{ $t('Konfiguracja') }}</dt>
            <dd class="mono">{{ v.data_dir }}/config.yml</dd>
          </dl>

          <div class="row wrap" style="gap: 8px">
            <a v-if="v.map_available" :href="v.map_url" target="_blank" rel="noopener" class="btn sm"><MapIcon /> {{ $t('Otwórz samą mapę') }}</a>
            <button v-if="v.update_available" class="btn sm" :disabled="busy" @click="toggle(true)">
              <Loader2 v-if="busy" class="spin" /><Download v-else /> {{ $t('Zaktualizuj teraz') }}
            </button>
          </div>

          <div v-if="restart" class="callout warn small">
            <RotateCcw />
            <div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div>
            <button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button>
          </div>
          <div v-else-if="!v.map_available" class="callout info small">
            <Info /><div>{{ $t('Mapa pojawi się po pierwszym uruchomieniu serwera z squaremap. Wygenerowanie kafelków może zająć chwilę.') }}</div>
          </div>
        </template>
        <div v-else-if="v.installed" class="tiny muted">{{ $t('squaremap jest zainstalowany, ale wyłączony – strona pokazuje tylko statystyki.') }}</div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, BarChart3, Copy, Download, ExternalLink, Info, Loader2, Map as MapIcon, RotateCcw } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { toast, toastError } from '../store'
import { copyText } from '../utils'

const props = defineProps({ serverId: String, subdomain: String })
const v = ref(null)
const busy = ref(false)
const restart = ref(false)

const pageUrl = computed(() => v.value?.public_url || `/${props.subdomain}/stats`)
const fullUrl = computed(() => `${window.location.origin}${pageUrl.value}`)
const renderHint = computed(() => {
  const r = v.value?.render
  if (r?.rendering) return r.paused ? t('Render {w} jest wstrzymany, bo ktoś gra – wznowi się, gdy serwer opustoszeje.', { w: r.rendering })
    : t('Teraz renderuje się {w}.', { w: r.rendering })
  return t('Pełny render świata uruchamia się {m} min po wyjściu ostatniego gracza i wstrzymuje, gdy ktoś dołączy.', { m: Math.round((r?.delay || 120) / 60) })
})

async function load() {
  try { v.value = await api.get(`/servers/${props.serverId}/squaremap`) } catch (e) { toastError(e) }
}
onMounted(load)

async function copyLink() {
  if (await copyText(fullUrl.value)) toast(t('Skopiowano link'), 'success', 1800)
}

async function toggle(enabled) {
  const wasInstalled = !!v.value?.installed
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/squaremap`, { enabled })
    v.value = r
    restart.value = !!r.restart_required
    if (enabled) toast(wasInstalled ? t('Zaktualizowano squaremap') : t('Zainstalowano squaremap'), 'success')
    else toast(t('Wyłączono mapę – strona ze statystykami działa dalej'), 'success')
  } catch (e) { toastError(e) } finally { busy.value = false }
}

async function setIdleRender(on) {
  busy.value = true
  try {
    v.value = await api.post(`/servers/${props.serverId}/squaremap`, { enabled: v.value.enabled, idle_render: on })
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
.addon-btn { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; margin-left: -1px; height: auto; }
.info { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.callout:not(.warn):not(.info) { background: var(--surface-2); }
</style>
