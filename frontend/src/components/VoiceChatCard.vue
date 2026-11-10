<template>
  <section class="card">
    <div class="card-head">
      <Mic class="hi" /><h3>{{ $t('Czat głosowy') }}</h3>
      <span v-if="v?.installed && v.enabled" class="badge green"><span class="dot" />{{ $t('włączony') }}</span>
      <span v-else-if="v?.installed" class="badge">{{ $t('wyłączony') }}</span>
    </div>
    <div v-if="!v" class="card-body"><Loader2 class="spin" style="width: 16px" /></div>
    <div v-else-if="!v.supported" class="card-body small muted">
      {{ $t('Simple Voice Chat działa z silnikami obsługującymi mody lub pluginy (Paper, Purpur, Fabric, Quilt, Forge, NeoForge).') }}
    </div>
    <div v-else-if="!v.installed" class="card-body row wrap" style="gap: 14px">
      <div class="grow" style="min-width: 240px">
        <b>{{ $t('Simple Voice Chat') }}</b>
        <div class="small text-2">{{ $t('Czat głosowy w grze. Wszystkie serwery dzielą jeden port UDP') }} <span class="mono">{{ v.public_port }}</span> {{ $t('– panel sam kieruje graczy do właściwego serwera.') }}</div>
      </div>
      <button class="btn primary" :disabled="busy" @click="install"><Loader2 v-if="busy" class="spin" /><Download v-else /> {{ $t('Zainstaluj i włącz') }}</button>
    </div>
    <div v-else class="card-body col" style="gap: 14px">
      <Toggle :model-value="v.enabled" :disabled="busy" :label="$t('Przez wspólny port panelu')" :hint="$t('Gracze łączą się z czatem przez UDP {port}, panel przekierowuje ich do tego serwera.', { port: v.public_port })" @update:model-value="toggle" />
      <dl v-if="v.enabled" class="info">
        <dt>{{ $t('Adres dla graczy') }}</dt><dd class="mono">{{ v.voice_host || '—' }}</dd>
        <dt>{{ $t('Port wewnętrzny') }}</dt><dd class="mono">{{ v.internal_port }}</dd>
        <dt>{{ $t('Aktywne połączenia') }}</dt><dd class="num">{{ v.sessions }}</dd>
      </dl>
      <div v-if="v.enabled && !v.public_host" class="callout warn small"><AlertTriangle /><div>{{ $t('Ustaw domenę bazową lub publiczny adres w') }} <router-link to="/settings">{{ $t('Ustawieniach panelu') }}</router-link> {{ $t('– inaczej klienci nie będą wiedzieć, gdzie się połączyć.') }}</div></div>
      <div v-if="!v.proxy_listening" class="callout warn small"><AlertTriangle /><div>{{ $t('Proxy czatu głosowego nie działa') }}{{ v.proxy_error ? `: ${v.proxy_error}` : '' }}.</div></div>
      <div v-if="restart" class="callout warn small"><RotateCcw /><div class="grow">{{ $t('Zmiana zadziała po restarcie serwera.') }}</div><button class="btn sm" @click="doRestart">{{ $t('Restartuj teraz') }}</button></div>
      <div v-else class="tiny muted">{{ $t('Pamiętaj o otwarciu portu UDP') }} <span class="mono">{{ v.public_port }}</span> {{ $t('na routerze/zaporze. Konfiguracja:') }} <span class="mono">{{ v.config_file }}</span></div>
    </div>
  </section>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { Mic, Loader2, Download, AlertTriangle, RotateCcw } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { toast, toastError } from '../store'

const props = defineProps({ serverId: String })
const v = ref(null)
const busy = ref(false)
const restart = ref(false)

async function load() { try { v.value = await api.get(`/servers/${props.serverId}/voice`) } catch (e) { toastError(e) } }
onMounted(load)
async function install() {
  busy.value = true
  try {
    const r = await api.post(`/servers/${props.serverId}/voice/install`)
    v.value = r
    restart.value = r.restart_required
    toast('Zainstalowano Simple Voice Chat', 'success')
  } catch (e) { toastError(e) } finally { busy.value = false }
}
async function toggle(enabled) {
  busy.value = true
  try { const r = await api.post(`/servers/${props.serverId}/voice`, { enabled }); v.value = r; restart.value = r.restart_required } catch (e) { toastError(e) } finally { busy.value = false }
}
async function doRestart() {
  try { await api.post(`/servers/${props.serverId}/power`, { action: 'restart' }); restart.value = false } catch (e) { toastError(e) }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.badge .dot { width: 7px; height: 7px; background: currentColor; }
.info { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; }
</style>
