<template>
  <section class="card">
    <div class="card-head">
      <MapIcon class="hi" /><h3>{{ $t('Mapa squaremap') }}</h3>
      <span v-if="v?.enabled && v.map_available" class="badge green"><span class="dot" />{{ $t('włączona') }}</span>
      <span v-else-if="v?.enabled" class="badge amber">{{ $t('oczekuje na start') }}</span>
      <span v-else-if="v" class="badge">{{ $t('wyłączona') }}</span>
    </div>
    <div v-if="!v" class="card-body"><Loader2 class="spin" style="width: 16px" /></div>
    <div v-else class="card-body col" style="gap: 14px">
      <Toggle :model-value="v.enabled" :disabled="busy || !v.supported || (!v.enabled && !v.available)"
              :label="$t('Włącz mapę squaremap')"
              :hint="$t('Jednym kliknięciem panel zainstaluje squaremap i przygotuje publiczną stronę z mapą.')"
              @update:model-value="toggle" />

      <div v-if="!v.supported" class="callout warn small">
        <AlertTriangle />
        <div>
          <b>{{ $t('Ten serwer nie obsługuje mapy.') }}</b>
          {{ $t('Ta opcja jest dostępna tylko dla serwerów z pluginami lub modami. Zmień silnik na Paper, Purpur, Fabric, Quilt, Forge lub NeoForge.') }}
        </div>
      </div>

      <div v-else-if="!v.available" class="callout warn small">
        <AlertTriangle />
        <div>{{ v.error || $t('Brak wersji squaremap dla tego silnika i wersji Minecrafta.') }}</div>
      </div>

      <template v-if="v.supported && v.available && (v.installed || v.enabled)">
        <dl class="info">
          <dt>{{ $t('Wersja') }}</dt>
          <dd>
            <span class="mono">{{ v.version || '—' }}</span>
            <span v-if="v.update_available" class="badge amber" style="margin-left: 8px">{{ $t('dostępna {v}', { v: v.latest_version }) }}</span>
          </dd>
          <dt>{{ $t('Strona publiczna') }}</dt>
          <dd><a :href="v.public_url" target="_blank" rel="noopener" class="mono">{{ v.public_url }} <ExternalLink /></a></dd>
          <dt>{{ $t('Plik konfiguracji') }}</dt>
          <dd class="mono">{{ v.data_dir }}/config.yml</dd>
        </dl>

        <div class="row wrap" style="gap: 8px">
          <a :href="v.public_url" target="_blank" rel="noopener" class="btn sm"><ExternalLink /> {{ $t('Otwórz stronę serwera') }}</a>
          <a v-if="v.map_available" :href="v.map_url" target="_blank" rel="noopener" class="btn sm ghost"><MapIcon /> {{ $t('Otwórz mapę') }}</a>
          <button v-if="v.update_available" class="btn sm" :disabled="busy" @click="toggle(true)">
            <Loader2 v-if="busy" class="spin" /><Download v-else /> {{ $t('Zaktualizuj teraz') }}
          </button>
        </div>

        <div v-if="v.enabled && !v.map_available" class="callout info small">
          <Info />
          <div>{{ $t('Mapa pojawi się po pierwszym uruchomieniu serwera z squaremap. Wygenerowanie kafelków może zająć chwilę.') }}</div>
        </div>
        <div v-else-if="!v.enabled" class="callout info small">
          <Info />
          <div>{{ $t('squaremap jest zainstalowany, ale wyłączony. Włącz go powyżej, aby mapa znów była dostępna.') }}</div>
        </div>

        <div v-if="restart" class="callout warn small">
          <RotateCcw />
          <div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div>
          <button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button>
        </div>
        <div v-else class="tiny muted">{{ $t('squaremap jest sprawdzany i automatycznie aktualizowany przy każdym starcie serwera.') }}</div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { Map as MapIcon, Loader2, AlertTriangle, Info, ExternalLink, Download, RotateCcw } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { toast, toastError } from '../store'

const props = defineProps({ serverId: String })
const v = ref(null)
const busy = ref(false)
const restart = ref(false)

async function load() {
  try { v.value = await api.get(`/servers/${props.serverId}/squaremap`) } catch (e) { toastError(e) }
}
onMounted(load)

async function toggle(enabled) {
  const wasInstalled = !!v.value?.installed
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/squaremap`, { enabled })
    v.value = r
    restart.value = !!r.restart_required
    if (enabled) toast(wasInstalled ? t('Zaktualizowano squaremap') : t('Zainstalowano squaremap'), 'success')
    else toast(t('Wyłączono squaremap'), 'success')
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
.hi { width: 16px; height: 16px; color: var(--muted); }
.badge .dot { width: 7px; height: 7px; background: currentColor; }
.info { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.info a { display: inline-flex; align-items: center; gap: 5px; }
.info a svg { width: 13px; height: 13px; }
</style>
